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
from email.utils import formatdate, make_msgid
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

from email_recipients import DEFAULT_RECIPIENT_CONFIG, RecipientError, formatted_addresses, resolve_recipients


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
    to_addrs: list[str],
    images: list[InlineImage],
) -> MIMEMultipart:
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    if from_addr:
        msg["From"] = from_addr
    if to_addrs:
        msg["To"] = ", ".join(to_addrs)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="local.ai-daily")

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(plain_text, "plain", "utf-8"))
    alternative.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alternative)

    for image in images:
        attach_inline_image(msg, image)
    return msg


def write_readme(
    package_dir: Path,
    html_path: Path,
    eml_path: Path,
    body_path: Path,
    subject: str,
    to_addrs: list[str],
    images: list[InlineImage],
    max_bytes: int,
) -> None:
    lines = [
        "AI 日报邮件发送包",
        "",
        f"源 HTML: {html_path}",
        f"邮件主题: {subject}",
        f"收件人数量: {len(to_addrs)}",
        "",
        "包内文件:",
        f"- {eml_path.name}: 已将图片改为 CID 内联的邮件草稿文件。",
        f"- {body_path.name}: 同步保存的 HTML，图片指向本包 images/ 下的压缩文件，便于浏览器预览或手动复制。",
        "- images/: 压缩后的图片文件，每张目标小于 1MB。",
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


def write_manifest(package_dir: Path, images: list[InlineImage]) -> None:
    data = {
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
        ]
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
    parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing output package directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    html_path = args.html.resolve()
    if not html_path.exists():
        raise SystemExit(f"Package failed: missing HTML file: {html_path}")
    if args.max_image_bytes <= 0:
        raise SystemExit("Package failed: --max-image-bytes must be positive.")

    try:
        recipients = resolve_recipients(args.recipient_list, args.to, args.recipient_config)
        to_addrs = formatted_addresses(recipients)
    except RecipientError as exc:
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

    try:
        images = process_images(
            html_path=html_path,
            soup_for_cid=soup_cid,
            soup_for_local=soup_local,
            package_dir=package_dir,
            max_bytes=args.max_image_bytes,
            timeout=args.remote_timeout,
        )
        body_html = str(soup_local)
        cid_html = str(soup_cid)
        body_path = package_dir / "body.html"
        body_path.write_text(body_html, encoding="utf-8")

        msg = build_eml(
            html=cid_html,
            plain_text=plain_fallback(soup_local),
            subject=subject,
            from_addr=args.from_addr or None,
            to_addrs=to_addrs,
            images=images,
        )
        eml_path = package_dir / f"{slugify(subject, html_path.stem)}.eml"
        eml_path.write_bytes(msg.as_bytes())
        write_readme(package_dir, html_path, eml_path, body_path, subject, to_addrs, images, args.max_image_bytes)
        write_manifest(package_dir, images)
    except Exception as exc:
        raise SystemExit(f"Package failed: {exc}") from exc

    print(f"Wrote package: {package_dir}")
    print(f"EML: {eml_path}")
    print(f"HTML: {body_path}")
    print(f"Images: {len(images)}")
    print(f"Recipients: {len(to_addrs)}")


if __name__ == "__main__":
    main()
