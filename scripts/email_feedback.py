#!/usr/bin/env python3
"""Email-safe feedback block and per-recipient personalization helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup

WORKFLOW_DIR = Path(__file__).resolve().parents[1]
if str(WORKFLOW_DIR) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_DIR))

from feedback_service.core import create_feedback_token
from feedback_service.routes import comment_url, normalize_base_url, open_pixel_url, rating_url


FEEDBACK_BLOCK_ATTR = "data-ai-daily-feedback"
RATING_ATTR = "data-ai-daily-rating"
COMMENT_ATTR = "data-ai-daily-comment"
TRACK_LINK_ATTR = "data-ai-daily-track-link"
TRACK_TITLE_ATTR = "data-ai-daily-track-title"


def _is_trackable_destination(value: object) -> bool:
    return str(value or "").strip().lower().startswith(("https://", "http://"))


def render_feedback_block() -> str:
    """Return an inert preview block; the email packager activates its links."""

    stars = "".join(
        f'''
          <td align="center" width="22" style="padding-left:{2 if score > 1 else 0}px;">
            <a class="rating-star" href="#" {RATING_ATTR}="{score}" title="{score} 星" style="display:block;color:#8B969D;text-decoration:none;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:21px;font-weight:400;">☆</a>
          </td>'''
        for score in range(1, 6)
    )
    return f'''
          <tr {FEEDBACK_BLOCK_ATTR}="1">
            <td style="padding:22px 44px 0 44px;">
              <table role="presentation" cellpadding="0" cellspacing="0" align="left">
                <tr>
                  <td style="padding:0 0 7px 0;font-size:13px;line-height:20px;color:#5F6A71;font-weight:500;white-space:nowrap;">今天这期怎么样？</td>
                </tr>
                <tr>
                  <td align="left" valign="middle" style="padding:0;">
                    <table role="presentation" cellpadding="0" cellspacing="0" align="left">
                      <tr>
                        {stars}
                        <td valign="middle" style="padding-left:15px;white-space:nowrap;">
                          <a href="#" {COMMENT_ATTR}="1" style="color:#6D777E;text-decoration:none;font-size:12px;line-height:21px;font-weight:400;"><span style="color:#4E7A8A;font-family:Georgia,serif;font-size:15px;padding-right:5px;">&#9998;</span>写一句建议</a>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
'''


def _block(soup: BeautifulSoup):
    return soup.find(attrs={FEEDBACK_BLOCK_ATTR: "1"})


def _keep_article_links_direct(soup: BeautifulSoup) -> None:
    """Remove packaging markers without replacing the original destination.

    Email clients cannot run a reliable background click beacon. Routing the
    article itself through the feedback service therefore makes that service a
    single point of failure. Source links intentionally remain direct; rating,
    comment and open events stay auxiliary.
    """

    for anchor in soup.find_all("a", attrs={TRACK_LINK_ATTR: "1"}):
        destination_url = str(anchor.get("href") or "").strip()
        if not _is_trackable_destination(destination_url):
            raise ValueError("Tracked article link must use http:// or https://.")
        anchor["target"] = "_blank"
        anchor["rel"] = "noopener noreferrer"
        del anchor[TRACK_LINK_ATTR]
        if anchor.has_attr(TRACK_TITLE_ATTR):
            del anchor[TRACK_TITLE_ATTR]


def remove_feedback(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    block = _block(soup)
    if block is not None:
        block.decompose()
    _keep_article_links_direct(soup)
    return str(soup)


def personalize_feedback(
    html: str,
    *,
    base_url: str,
    issue_id: str,
    recipient_key: str,
    secret: str,
) -> tuple[str, str]:
    """Activate feedback links and add an invisible open pixel.

    The returned token contains only an issue ID and an irreversible recipient
    identifier. The raw email address never reaches the feedback service.
    """

    base_url = normalize_base_url(base_url)

    token = create_feedback_token(issue_id, recipient_key, secret)
    encoded_token = quote(token, safe="")
    soup = BeautifulSoup(html, "html.parser")
    block = _block(soup)
    if block is None:
        raise ValueError("Email HTML is missing the feedback block.")

    for anchor in block.find_all("a", attrs={RATING_ATTR: True}):
        score = str(anchor.get(RATING_ATTR) or "")
        if score not in {"1", "2", "3", "4", "5"}:
            raise ValueError("Email feedback block contains an invalid score.")
        anchor["href"] = rating_url(base_url, encoded_token, int(score))
        anchor["target"] = "_blank"
        anchor["rel"] = "noopener"
        del anchor[RATING_ATTR]

    comment = block.find("a", attrs={COMMENT_ATTR: True})
    if comment is None:
        raise ValueError("Email feedback block is missing the comment link.")
    comment["href"] = comment_url(base_url, encoded_token)
    comment["target"] = "_blank"
    comment["rel"] = "noopener"
    del comment[COMMENT_ATTR]
    del block[FEEDBACK_BLOCK_ATTR]

    _keep_article_links_direct(soup)

    body = soup.body
    if body is None:
        raise ValueError("Email HTML is missing a body element.")
    pixel = soup.new_tag("img")
    pixel["src"] = open_pixel_url(base_url, encoded_token)
    pixel["width"] = "1"
    pixel["height"] = "1"
    pixel["alt"] = ""
    pixel["data-ai-daily-open"] = "1"
    pixel["style"] = "display:block;width:1px;height:1px;border:0;overflow:hidden;"
    body.append(pixel)
    return str(soup), token
