"""Small pure HTML string helpers shared by page-rendering routes."""

from __future__ import annotations

import re


def add_body_attribute(html: str, name: str, value: str) -> str:
    pattern = re.compile(r"<body([^>]*)>", re.IGNORECASE)

    def replace(match: re.Match) -> str:
        attrs = match.group(1) or ""
        if re.search(rf"\s{name}=", attrs, re.IGNORECASE):
            return match.group(0)
        return f'<body{attrs} {name}="{value}">'

    return pattern.sub(replace, html, count=1)


def add_body_class(html: str, class_name: str) -> str:
    pattern = re.compile(r"<body([^>]*)>", re.IGNORECASE)

    def replace(match: re.Match) -> str:
        attrs = match.group(1) or ""
        class_match = re.search(r'\sclass=(["\'])(.*?)\1', attrs, re.IGNORECASE)
        if not class_match:
            return f'<body{attrs} class="{class_name}">'
        classes = class_match.group(2).split()
        if class_name in classes:
            return match.group(0)
        next_value = " ".join([*classes, class_name])
        start, end = class_match.span(2)
        return f"<body{attrs[:start]}{next_value}{attrs[end:]}>"

    return pattern.sub(replace, html, count=1)


def inject_before_head_close(html: str, markup: str) -> str:
    if "</head>" in html.lower():
        return re.sub(r"</head>", markup + "</head>", html, count=1, flags=re.IGNORECASE)
    return markup + html


def prepare_embedded_html(html: str) -> str:
    html = add_body_class(html, "embedded")
    return inject_before_head_close(
        html,
        """
<style id="flashloan-embedded-style">
  body.embedded { padding: 0 !important; }
  body.embedded > header { display: none !important; }
  body.embedded .chain-selector-shell { display: none !important; }
  body.embedded main { margin-top: 0 !important; }
</style>
<script>
(function(){
  if (window.parent === window) return;
  document.addEventListener("click", function(event) {
    var link = event.target && event.target.closest ? event.target.closest("a[href]") : null;
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target && link.target !== "_self") return;
    if (link.hasAttribute("download")) return;
    var url = new URL(link.getAttribute("href"), window.location.origin);
    if (url.origin !== window.location.origin || url.pathname.indexOf("/api/") === 0 || url.pathname.indexOf("/static/") === 0) return;
    event.preventDefault();
    window.parent.postMessage({type: "flashloan:navigate", href: url.pathname + url.search + url.hash}, window.location.origin);
  });
})();
</script>
""",
    )


__all__ = [
    "add_body_attribute",
    "add_body_class",
    "inject_before_head_close",
    "prepare_embedded_html",
]
