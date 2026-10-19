#!/usr/bin/env python3
"""Create a portable email-send package from an HTML email.

Outputs:
- .eml draft with images embedded by Content-ID
- body.html with local compressed image references
- images/ with each image compressed below the configured byte limit
- README.txt with simple usage notes

This script does not send email and does not contain SMTP credentials.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
from dataclasses import dataclass
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import policy
from email.parser import BytesParser
from email.utils import formataddr, formatdate, getaddresses, make_msgid
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

from email_recipients import (
    DEFAULT_RECIPIENT_CONFIG,
    RECIPIENT_SNAPSHOT_NAME,
    RecipientError,
    formatted_addresses,
    resolve_recipient_delivery,
    write_recipient_snapshot,
)
from email_feedback import personalize_feedback, remove_feedback
from feedback_service.routes import HEALTH_PATH, absolute_url


DEFAULT_MAX_BYTES = 1_000_000


class PackageError(Exception):
    pass


@dataclass
class InlineImage:
    source: str
    cid: str
    filename: str
    content_type: str
    output_path: Path
    original_bytes: int
    final_bytes: int
    original_size: tuple[int, int] | None
    final_size: tuple[int, int] | None


@dataclass(frozen=True)
class FeedbackRuntime:
    enabled: bool
    base_url: str = ""
    issue_id: str = ""
    secret: str = ""
    features_enabled: bool = False
    health_status: str = "disabled"
    health_error: str = ""


def normalized_recipient_email(value: str) -> str:
    """Return the canonical address for a display-address or bare email."""

    addresses = [email.strip().lower() for _, email in getaddresses([value]) if email.strip()]
    if len(addresses) != 1:
        raise PackageError(f"Expected exactly one recipient email, got: {value!r}")
    return addresses[0]


def load_conversation_root_message_ids(package_dir: Path) -> dict[str, str]:
    """Map each recipient in a prior personalized package to its Message-ID.

    Mailing a personalized newsletter means that the root email has a distinct
    Message-ID for each recipient.  The follow-up issue must therefore look up
    the matching recipient's root rather than reuse one recipient's ID for the
    entire list.
    """

    messages_dir = package_dir / "messages"
    if not messages_dir.is_dir():
        raise PackageError(
            f"Conversation root package has no personalized messages directory: {messages_dir}"
        )

    roots: dict[str, str] = {}
    for path in sorted(messages_dir.glob("*.eml")):
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        recipients = [email.strip().lower() for _, email in getaddresses(message.get_all("To", [])) if email.strip()]
        message_id = str(message.get("Message-ID") or "").strip()
        if len(recipients) != 1 or not message_id:
            raise PackageError(f"Invalid personalized conversation root message: {path}")
        recipient = recipients[0]
        if recipient in roots:
            raise PackageError(f"Duplicate recipient in conversation root package: {recipient}")
        roots[recipient] = message_id

    if not roots:
        raise PackageError(f"Conversation root package has no usable personalized messages: {messages_dir}")
    return roots


def slugify(value: str, fallback: str = "email") -> str:
    value = value.strip().lower()
    value = re.sub(r"[\u2018\u2019\u201c\u201d'\"`]", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or fallback


def is_remote_src(src: str) -> bool:
    return src.startswith(("http://", "https://"))


def is_data_uri(src: str) -> bool:
    return src.startswith("data:")


def resolve_local_src(src: str, html_path: Path) -> Path:
    parsed = urlparse(src)
    if parsed.scheme and parsed.scheme not in {"file"}:
        raise PackageError(f"Unsupported image src scheme: {src}")
    raw_path = unquote(parsed.path if parsed.scheme == "file" else src.split("#", 1)[0].split("?", 1)[0])
    path = Path(raw_path)
    if not path.is_absolute():
        path = html_path.parent / path
    return path.resolve()


def read_image_bytes(src: str, html_path: Path, timeout: int) -> tuple[bytes, str]:
    if is_data_uri(src):
        header, _, payload = src.partition(",")
        if ";base64" not in header:
            raise PackageError("Only base64 data URI images are supported.")
        content_type = header[5:].split(";", 1)[0] or "application/octet-stream"
        return base64.b64decode(payload), content_type

    if is_remote_src(src):
        req = Request(src, headers={"User-Agent": "Mozilla/5.0 ai-daily-email-packager/0.1"})
        with urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get_content_type() or "application/octet-stream"
            return response.read(), content_type

    local_path = resolve_local_src(src, html_path)
    if not local_path.exists():
        raise PackageError(f"Missing local image: {local_path}")
    guessed = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    return local_path.read_bytes(), guessed


def image_size(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except UnidentifiedImageError:
        return None


def save_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def has_alpha(img: Image.Image) -> bool:
    if img.mode in {"RGBA", "LA"}:
        return True
    if img.mode == "P" and "transparency" in img.info:
        return True
    return False


def flatten_to_rgb(img: Image.Image) -> Image.Image:
    if has_alpha(img):
        rgba = img.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def save_jpeg_bytes(img: Image.Image, quality: int) -> bytes:
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
    return output.getvalue()


def compress_with_pillow(data: bytes, max_bytes: int) -> tuple[bytes, str]:
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        rgb = flatten_to_rgb(img)

        if max(rgb.size) > 1800:
            ratio = 1800 / max(rgb.size)
            rgb = rgb.resize((max(1, int(rgb.width * ratio)), max(1, int(rgb.height * ratio))), Image.Resampling.LANCZOS)

        for quality in [92, 88, 84, 80, 76, 72, 68, 64, 58, 52, 46]:
            encoded = save_jpeg_bytes(rgb, quality)
            if len(encoded) <= max_bytes:
                return encoded, "image/jpeg"

        working = rgb
        quality = 44
        while max(working.size) > 480:
            working = working.resize(
                (max(1, int(working.width * 0.85)), max(1, int(working.height * 0.85))),
                Image.Resampling.LANCZOS,
            )
            encoded = save_jpeg_bytes(working, quality)
            if len(encoded) <= max_bytes:
                return encoded, "image/jpeg"

        encoded = save_jpeg_bytes(working, 38)
        if len(encoded) > max_bytes:
            raise PackageError(f"Unable to compress image below {max_bytes} bytes.")
        return encoded, "image/jpeg"


def compress_image(data: bytes, content_type: str, max_bytes: int) -> tuple[bytes, str]:
    if len(data) <= max_bytes and content_type in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        return data, content_type
    try:
        return compress_with_pillow(data, max_bytes)
    except UnidentifiedImageError as exc:
        if len(data) <= max_bytes:
            return data, content_type
        raise PackageError("Unsupported image format and file is larger than max bytes.") from exc


def extension_for_content_type(content_type: str) -> str:
    if content_type == "image/jpeg":
        return ".jpg"
    if content_type == "image/png":
        return ".png"
    if content_type == "image/webp":
        return ".webp"
    if content_type == "image/gif":
        return ".gif"
    return mimetypes.guess_extension(content_type) or ".bin"


def make_cid(index: int, src: str, data: bytes) -> str:
    digest = hashlib.sha256((src + "\n").encode("utf-8") + data[:1024]).hexdigest()[:12]
    return f"ai-daily-image-{index}-{digest}@local"


def extract_subject(soup: BeautifulSoup, html_path: Path, override: str | None) -> str:
    if override:
        return override
    if soup.title and soup.title.string and soup.title.string.strip():
        return soup.title.string.strip()
    heading = soup.find(["h1", "h2"])
    if heading:
        text = heading.get_text(" ", strip=True)
        if text:
            return text
    return html_path.stem


def plain_fallback(soup: BeautifulSoup) -> str:
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:4000] or "HTML email content is attached in the HTML part."


def process_images(
    html_path: Path,
    soup_for_cid: BeautifulSoup,
    soup_for_local: BeautifulSoup,
    package_dir: Path,
    max_bytes: int,
    timeout: int,
) -> list[InlineImage]:
    images_dir = package_dir / "images"
    img_pairs = list(zip(soup_for_cid.find_all("img"), soup_for_local.find_all("img")))
    processed: dict[str, InlineImage] = {}
    images: list[InlineImage] = []

    for index, (cid_tag, local_tag) in enumerate(img_pairs, start=1):
        src = str(cid_tag.get("src") or "").strip()
        if not src or src.startswith("cid:"):
            continue

        if src in processed:
            image = processed[src]
            cid_tag["src"] = f"cid:{image.cid}"
            local_tag["src"] = f"images/{image.filename}"
            continue

        raw_data, source_content_type = read_image_bytes(src, html_path, timeout)
        original_size = image_size(raw_data)
        compressed, final_content_type = compress_image(raw_data, source_content_type, max_bytes)
        final_size = image_size(compressed)
        cid = make_cid(index, src, compressed)
        filename = f"{slugify(cid.split('@', 1)[0], f'image-{index}')}{extension_for_content_type(final_content_type)}"
        output_path = images_dir / filename
        save_bytes(output_path, compressed)

        image = InlineImage(
            source=src,
            cid=cid,
            filename=filename,
            content_type=final_content_type,
            output_path=output_path,
            original_bytes=len(raw_data),
            final_bytes=len(compressed),
            original_size=original_size,
            final_size=final_size,
        )
        processed[src] = image
        images.append(image)
        cid_tag["src"] = f"cid:{cid}"
        local_tag["src"] = f"images/{filename}"

    return images


def attach_inline_image(msg: MIMEMultipart, image: InlineImage) -> None:
    maintype, subtype = image.content_type.split("/", 1) if "/" in image.content_type else ("application", "octet-stream")
    part = MIMEBase(maintype, subtype)
    part.set_payload(image.output_path.read_bytes())
    encoders.encode_base64(part)
    part.add_header("Content-ID", f"<{image.cid}>")
    part.add_header("Content-Disposition", "inline", filename=image.filename)
    part.add_header("Content-Location", image.filename)
    msg.attach(part)


def build_eml(
    html: str,
    plain_text: str,
    subject: str,
    from_addr: str | None,
    from_name: str | None,
    to_addrs: list[str],
    images: list[InlineImage],
    in_reply_to: str | None = None,
    references: str | None = None,
) -> MIMEMultipart:
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    if from_addr:
        msg["From"] = formataddr((from_name.strip(), from_addr)) if from_name and from_name.strip() else from_addr
    if to_addrs:
        msg["To"] = ", ".join(to_addrs)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="local.ai-daily")
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(plain_text, "plain", "utf-8"))
    alternative.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alternative)

    for image in images:
        attach_inline_image(msg, image)
    return msg


def feedback_service_health(base_url: str, timeout: float) -> tuple[bool, str]:
    """Return whether the optional feedback service is ready for this package."""

    request = Request(
        absolute_url(base_url, HEALTH_PATH),
        headers={"Accept": "application/json", "User-Agent": "ai-daily-packager/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            payload = json.loads(response.read(64_000).decode("utf-8"))
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if status != 200:
        return False, f"HTTP {status}"
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return False, "health response did not contain ok=true"
    return True, ""


def resolve_feedback_runtime(args: argparse.Namespace) -> FeedbackRuntime:
    if args.disable_feedback:
        if args.require_feedback:
            raise PackageError("--disable-feedback cannot be combined with --require-feedback.")
        return FeedbackRuntime(enabled=False)

    base_url = str(args.feedback_base_url or "").strip().rstrip("/")
    secret = str(os.environ.get("AI_DAILY_FEEDBACK_SECRET") or "").strip()
    issue_id = str(args.feedback_issue or "").strip()
    configured = bool(base_url or secret or issue_id)
    if not configured:
        if args.require_feedback:
            raise PackageError(
                "Feedback is required but not configured. Set AI_DAILY_FEEDBACK_BASE_URL, "
                "AI_DAILY_FEEDBACK_SECRET and --feedback-issue."
            )
        return FeedbackRuntime(enabled=False)
    if not base_url or not secret or not issue_id:
        raise PackageError(
            "Incomplete feedback configuration. Base URL, AI_DAILY_FEEDBACK_SECRET and issue ID are all required."
        )
    if len(secret) < 16:
        raise PackageError("AI_DAILY_FEEDBACK_SECRET must be at least 16 characters.")
    if not base_url.startswith(("https://", "http://")):
        raise PackageError("Feedback base URL must start with http:// or https://.")
    if args.skip_feedback_health_check:
        return FeedbackRuntime(
            enabled=True,
            base_url=base_url,
            issue_id=issue_id,
            secret=secret,
            features_enabled=True,
            health_status="skipped",
        )

    healthy, error = feedback_service_health(base_url, args.feedback_health_timeout)
    if not healthy and args.require_feedback:
        raise PackageError(f"Feedback is required but its health check failed: {error}")
    return FeedbackRuntime(
        enabled=True,
        base_url=base_url,
        issue_id=issue_id,
        secret=secret,
        features_enabled=healthy,
        health_status="healthy" if healthy else "unavailable",
        health_error=error,
    )


def build_personalized_messages(
    *,
    package_dir: Path,
    html: str,
    plain_text: str,
    subject: str,
    from_addr: str | None,
    from_name: str | None,
    recipients: list[dict[str, str]],
    images: list[InlineImage],
    feedback: FeedbackRuntime,
    conversation_root_message_ids: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    if not feedback.enabled:
        return []
    messages_dir = package_dir / "messages"
    messages_dir.mkdir(parents=True, exist_ok=True)
    output: list[dict[str, object]] = []
    for index, recipient in enumerate(recipients, start=1):
        if feedback.features_enabled:
            recipient_html, _ = personalize_feedback(
                html,
                base_url=feedback.base_url,
                issue_id=feedback.issue_id,
                recipient_key=recipient["email"],
                secret=feedback.secret,
            )
        else:
            # Preserve one-message-per-recipient delivery and weekly thread
            # headers even when the optional feedback service is unavailable.
            recipient_html = remove_feedback(html)
        address = formatted_addresses([recipient])[0]
        root_message_id = None
        if conversation_root_message_ids is not None:
            recipient_email = normalized_recipient_email(recipient["email"])
            root_message_id = conversation_root_message_ids.get(recipient_email)
            # Midweek subscribers have no earlier message to reference. Send
            # their own new message, never another recipient's thread ID.
        message = build_eml(
            html=recipient_html,
            plain_text=plain_text,
            subject=subject,
            from_addr=from_addr,
            from_name=from_name,
            to_addrs=[address],
            images=images,
            in_reply_to=root_message_id,
            references=root_message_id,
        )
        path = messages_dir / f"message-{index:03d}.eml"
        path.write_bytes(message.as_bytes())
        output.append(
            {
                "index": index,
                "path": str(path.relative_to(package_dir)),
                "in_reply_to": root_message_id,
            }
        )
    return output


def write_readme(
    package_dir: Path,
    html_path: Path,
    eml_path: Path,
    body_path: Path,
    subject: str,
    delivery: dict,
    images: list[InlineImage],
    max_bytes: int,
    feedback: FeedbackRuntime,
    personalized_messages: list[dict[str, object]],
    conversation_root_package: Path | None,
) -> None:
    lines = [
        "AI 日报邮件发送包",
        "",
        f"源 HTML: {html_path}",
        f"邮件主题: {subject}",
        f"收件人数量: {delivery['recipient_count']}",
        "",
        "包内文件:",
        f"- {eml_path.name}: 已将图片改为 CID 内联的邮件草稿文件。",
        f"- {body_path.name}: 同步保存的 HTML，图片指向本包 images/ 下的压缩文件，便于浏览器预览或手动复制。",
        "- images/: 压缩后的图片文件，每张目标小于 1MB。",
        f"- {RECIPIENT_SNAPSHOT_NAME}: 本次打包冻结的收件人快照；发送时只使用这一份名单。",
        "",
        "使用建议:",
        "1. 把整个文件夹复制到公司电脑。",
        "2. 优先双击 .eml，用 Foxmail 或系统默认邮件客户端打开，检查图片是否显示。",
        "3. 如果 .eml 不能正常作为草稿打开，可打开 body.html 预览，再复制正文到 Foxmail；必要时从 images/ 手动插入图片。",
        "4. 发送前请再次检查收件人、主题、图片显示和链接跳转。",
        "",
        f"图片压缩上限: {max_bytes} bytes",
        f"图片数量: {len(images)}",
    ]
    if feedback.enabled and feedback.features_enabled:
        lines.extend(
            [
                "",
                "反馈与阅读统计:",
                f"- 已启用匿名个性化，期次 ID: {feedback.issue_id}。",
                f"- messages/ 内有 {len(personalized_messages)} 封真实发送邮件；自动 SMTP 发送器会逐封发送。",
                f"- {eml_path.name} 和 body.html 只用于预览，不含追踪像素，也不会污染阅读率。",
                "- 请不要手工群发顶层 .eml，否则不会产生阅读率和按人去重的反馈数据。",
            ]
        )
    elif feedback.enabled:
        lines.extend(
            [
                "",
                "反馈与阅读统计: 本次健康检查未通过，已自动降级。",
                f"- 原因: {feedback.health_error or feedback.health_status}",
                "- 新闻来源保持原文直链；评分、建议和打开统计已从本次发送包移除。",
                f"- messages/ 内仍有 {len(personalized_messages)} 封逐收件人邮件，收件人隔离与会话聚合不受影响。",
            ]
        )
    else:
        lines.extend(["", "反馈与阅读统计: 未启用；邮件包不含远程追踪像素或反馈链接。"])
    if conversation_root_package:
        lines.extend(
            [
                "",
                "会话聚合:",
                f"- 老收件人引用本周根邮件包；本周新加入的收件人从当前邮件开始: {conversation_root_package}",
                "- 请保持同一周的邮件主题完全一致；周一为根邮件，后续工作日为跟帖。",
            ]
        )
    group = delivery.get("recipient_group")
    if group:
        lines.extend(
            [
                f"名单组: {group['name']} ({group['id']}, v{group['version']})",
                f"名单指纹: {delivery['fingerprint']}",
            ]
        )
    elif delivery.get("manual_recipient_count"):
        lines.append("名单来源: 临时手工收件人")
    to_addrs = formatted_addresses(delivery["recipients"])
    if to_addrs:
        lines.append("")
        lines.append("收件人:")
        for address in to_addrs:
            lines.append(f"- {address}")
    for idx, image in enumerate(images, start=1):
        lines.append(
            f"- {idx}. {image.filename}: {image.original_bytes} -> {image.final_bytes} bytes, CID={image.cid}"
        )
    (package_dir / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manifest(
    package_dir: Path,
    images: list[InlineImage],
    delivery: dict,
    feedback: FeedbackRuntime,
    personalized_messages: list[dict[str, object]],
    conversation_root_package: Path | None,
) -> None:
    data = {
        "recipient_delivery": {
            "recipient_group": delivery.get("recipient_group"),
            "recipient_count": delivery["recipient_count"],
            "fingerprint": delivery["fingerprint"],
        },
        "images": [
            {
                "source": image.source,
                "cid": image.cid,
                "filename": image.filename,
                "content_type": image.content_type,
                "original_bytes": image.original_bytes,
                "final_bytes": image.final_bytes,
                "original_size": image.original_size,
                "final_size": image.final_size,
            }
            for image in images
        ],
        "personalized_delivery": {
            "enabled": feedback.enabled,
            "feedback_base_url": feedback.base_url if feedback.enabled else None,
            "issue_id": feedback.issue_id if feedback.enabled else None,
            "feedback_features_enabled": feedback.features_enabled,
            "feedback_health_status": feedback.health_status,
            "feedback_health_error": feedback.health_error or None,
            "source_links": "direct",
            "message_count": len(personalized_messages),
            "messages": personalized_messages,
        },
        "conversation": {
            "enabled": conversation_root_package is not None,
            "root_package": str(conversation_root_package) if conversation_root_package else None,
        },
    }
    (package_dir / "manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create .eml package with CID inline images from an HTML email.")
    parser.add_argument("html", type=Path, help="Input HTML email path.")
    parser.add_argument("--out-dir", type=Path, help="Output package directory.")
    parser.add_argument("--subject", help="Override email subject. Defaults to <title>.")
    parser.add_argument(
        "--from-addr",
        default=os.environ.get("AI_DAILY_FROM_ADDR", ""),
        help="From header. Use empty string to omit.",
    )
    parser.add_argument(
        "--from-name",
        default=os.environ.get("AI_DAILY_FROM_NAME", ""),
        help="Optional display name for the From header. The SMTP envelope remains --from-addr.",
    )
    parser.add_argument("--to", action="append", default=[], help="Recipient header. Can be repeated.")
    parser.add_argument(
        "--recipient-list",
        choices=["test", "prod"],
        help="Use a named recipient list from ai_daily_workflow/config/recipients.json.",
    )
    parser.add_argument(
        "--recipient-config",
        type=Path,
        default=DEFAULT_RECIPIENT_CONFIG,
        help="Recipient config JSON path.",
    )
    parser.add_argument("--max-image-bytes", type=int, default=DEFAULT_MAX_BYTES, help="Max bytes per inline image.")
    parser.add_argument("--remote-timeout", type=int, default=20, help="Timeout for remote image download.")
    parser.add_argument(
        "--feedback-base-url",
        default=os.environ.get("AI_DAILY_FEEDBACK_BASE_URL", ""),
        help="Reachable feedback-service URL. The signing secret is read only from AI_DAILY_FEEDBACK_SECRET.",
    )
    parser.add_argument("--feedback-issue", help="Stable issue ID, normally YYYY-MM-DD.")
    parser.add_argument(
        "--conversation-root-package",
        type=Path,
        help=(
            "Prior personalized package used as this email's conversation root. "
            "A recipient-specific Message-ID is looked up from its messages/ directory."
        ),
    )
    parser.add_argument("--disable-feedback", action="store_true", help="Build without feedback or open tracking.")
    parser.add_argument("--require-feedback", action="store_true", help="Fail if feedback is not fully configured.")
    parser.add_argument(
        "--feedback-health-timeout",
        type=float,
        default=4.0,
        help="Seconds allowed for the optional feedback-service health check.",
    )
    parser.add_argument(
        "--skip-feedback-health-check",
        action="store_true",
        help="Keep feedback features without probing the service. Intended for controlled tests only.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing output package directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    html_path = args.html.resolve()
    if not html_path.exists():
        raise SystemExit(f"Package failed: missing HTML file: {html_path}")
    if args.max_image_bytes <= 0:
        raise SystemExit("Package failed: --max-image-bytes must be positive.")
    if args.feedback_health_timeout <= 0:
        raise SystemExit("Package failed: --feedback-health-timeout must be positive.")

    try:
        delivery = resolve_recipient_delivery(args.recipient_list, args.to, args.recipient_config)
        to_addrs = formatted_addresses(delivery["recipients"])
        feedback = resolve_feedback_runtime(args)
    except (RecipientError, PackageError) as exc:
        raise SystemExit(f"Package failed: {exc}") from exc

    package_dir = (args.out_dir or Path("email_packages") / html_path.stem).resolve()
    if package_dir.exists() and args.overwrite:
        shutil.rmtree(package_dir)
    elif package_dir.exists() and any(package_dir.iterdir()):
        raise SystemExit(f"Package failed: output directory already exists: {package_dir}. Use --overwrite.")
    package_dir.mkdir(parents=True, exist_ok=True)

    source_html = html_path.read_text(encoding="utf-8")
    soup_cid = BeautifulSoup(source_html, "html.parser")
    soup_local = BeautifulSoup(source_html, "html.parser")
    subject = extract_subject(soup_cid, html_path, args.subject)
    conversation_root_package = (
        args.conversation_root_package.resolve() if args.conversation_root_package else None
    )
    try:
        conversation_root_message_ids = (
            load_conversation_root_message_ids(conversation_root_package)
            if conversation_root_package
            else None
        )
    except (OSError, PackageError) as exc:
        raise SystemExit(f"Package failed: {exc}") from exc

    try:
        images = process_images(
            html_path=html_path,
            soup_for_cid=soup_cid,
            soup_for_local=soup_local,
            package_dir=package_dir,
            max_bytes=args.max_image_bytes,
            timeout=args.remote_timeout,
        )
        cid_html_with_feedback = str(soup_cid)
        body_html = remove_feedback(str(soup_local))
        preview_cid_html = remove_feedback(cid_html_with_feedback)
        body_path = package_dir / "body.html"
        body_path.write_text(body_html, encoding="utf-8")
        plain_text = plain_fallback(BeautifulSoup(body_html, "html.parser"))

        msg = build_eml(
            html=preview_cid_html,
            plain_text=plain_text,
            subject=subject,
            from_addr=args.from_addr or None,
            from_name=args.from_name or None,
            to_addrs=to_addrs,
            images=images,
        )
        eml_path = package_dir / f"{slugify(subject, html_path.stem)}.eml"
        eml_path.write_bytes(msg.as_bytes())
        personalized_messages = build_personalized_messages(
            package_dir=package_dir,
            html=cid_html_with_feedback,
            plain_text=plain_text,
            subject=subject,
            from_addr=args.from_addr or None,
            from_name=args.from_name or None,
            recipients=delivery["recipients"],
            images=images,
            feedback=feedback,
            conversation_root_message_ids=conversation_root_message_ids,
        )
        snapshot_path = package_dir / RECIPIENT_SNAPSHOT_NAME
        write_recipient_snapshot(snapshot_path, delivery)
        write_readme(
            package_dir,
            html_path,
            eml_path,
            body_path,
            subject,
            delivery,
            images,
            args.max_image_bytes,
            feedback,
            personalized_messages,
            conversation_root_package,
        )
        write_manifest(
            package_dir,
            images,
            delivery,
            feedback,
            personalized_messages,
            conversation_root_package,
        )
    except Exception as exc:
        raise SystemExit(f"Package failed: {exc}") from exc

    print(f"Wrote package: {package_dir}")
    print(f"EML: {eml_path}")
    print(f"HTML: {body_path}")
    print(f"Images: {len(images)}")
    print(f"Recipients: {delivery['recipient_count']}")
    print(
        "Feedback features: "
        f"{'enabled' if feedback.features_enabled else 'degraded' if feedback.enabled else 'disabled'}"
    )
    print("Source links: direct")
    if feedback.enabled:
        print(f"Personalized messages: {len(personalized_messages)}")
    if conversation_root_package:
        print(f"Conversation root package: {conversation_root_package}")
    print(f"Recipient snapshot: {snapshot_path}")
    if delivery["recipient_group"]:
        group = delivery["recipient_group"]
        print(f"Recipient group: {group['name']} ({group['id']} v{group['version']})")


if __name__ == "__main__":
    main()
