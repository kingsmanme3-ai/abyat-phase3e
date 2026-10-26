"""نموذج التقرير القياسي (3ج): ترويسة الشركة، بيانات التقرير، المتن، الجداول، المرفقات، ملاحظات الإدارة، التوقيع.
دالة نقية تُرجع HTML كاملاً؛ يُعرض في المتصفح للطباعة أو يُحوَّل إلى PDF بـ WeasyPrint على الخادم."""
import base64
from functools import lru_cache
from html import escape as _e
from pathlib import Path

from .authoring import PRIORITY_LABELS, STATUS_LABELS

CSS = """
@font-face{font-family:"Tajawal";font-weight:400;src:url("__FB__Tajawal-Regular.woff2") format("woff2")}
@font-face{font-family:"Tajawal";font-weight:500;src:url("__FB__Tajawal-Medium.woff2") format("woff2")}
@font-face{font-family:"Tajawal";font-weight:700;src:url("__FB__Tajawal-Bold.woff2") format("woff2")}
@font-face{font-family:"TajawalLatin";font-weight:400;src:url("__FB__Tajawal-Regular-latin.woff2") format("woff2")}
@font-face{font-family:"TajawalLatin";font-weight:700;src:url("__FB__Tajawal-Bold-latin.woff2") format("woff2")}
@page{size:A4;margin:14mm 13mm 18mm;
  @bottom-center{content:counter(page) " / " counter(pages);direction:ltr;unicode-bidi:embed;font:9pt "Tajawal","Noto Sans Arabic","DejaVu Sans",sans-serif;color:#64748b}
  @bottom-right{content:"__SERIAL__";font:9pt "TajawalLatin","DejaVu Sans",sans-serif;color:#64748b}}
*{box-sizing:border-box}
body{margin:0;font-family:"TajawalLatin","Tajawal","Noto Sans Arabic","Noto Naskh Arabic","DejaVu Sans",Tahoma,sans-serif;
  font-size:11pt;color:#1e293b;line-height:1.6}
.head{display:flex;justify-content:space-between;align-items:center;gap:12px;background:#1a3e5f;color:#fff;border-radius:10px;padding:10px 16px}
.head .logo{height:15mm;width:auto;display:block}
.head .co b{display:block;font-size:17pt;line-height:1.3}.head .co span{font-size:10pt;opacity:.9}
.head .doc{text-align:left;direction:ltr}.head .doc small{display:block;font-size:9pt;opacity:.9;direction:rtl;text-align:left}
.head .doc strong{font-size:14pt;letter-spacing:.5px}
.banner{margin:10px 0 0;padding:6px 12px;border-radius:8px;background:#fef3c7;color:#92400e;font-size:10pt}
h1{font-size:16pt;margin:14px 0 8px;color:#0f172a}
h2{font-size:12pt;margin:16px 0 6px;color:#1a3e5f;border-bottom:2px solid #b9d3ea;padding-bottom:3px;break-after:avoid}
table{width:100%;border-collapse:collapse}
table.meta td{border:1px solid #e2e8f0;padding:5px 9px;font-size:10pt;vertical-align:top}
table.meta td.k{background:#eef4fa;color:#475569;width:15%;white-space:nowrap}
table.meta td.v{width:35%}
.fld{margin:0 0 8px;break-inside:avoid}
.fld .k{font-size:9.5pt;color:#64748b}
.fld .v{border:1px solid #e2e8f0;background:#f8fafc;border-radius:6px;padding:5px 9px;white-space:pre-wrap;min-height:1.9em}
table.data th{background:#eef4fa;color:#1a3e5f;font-size:9.5pt;text-align:right;border:1px solid #e2e8f0;padding:4px 8px}
table.data td{border:1px solid #e2e8f0;padding:4px 8px;font-size:10pt;vertical-align:top}
table.data tr{break-inside:avoid}
.gallery{display:flex;flex-wrap:wrap;gap:8px}
.gallery figure{margin:0;width:calc(50% - 4px);border:1px solid #e2e8f0;border-radius:8px;padding:5px;break-inside:avoid;text-align:center}
.gallery img{max-width:100%;max-height:64mm;object-fit:contain}
.gallery figcaption{font-size:9pt;color:#475569;margin-top:3px}
.tag{display:inline-block;border-radius:8px;padding:0 7px;font-size:8.5pt;color:#fff}
ul.vids{margin:6px 0;padding-right:18px;font-size:10pt}
.notes{border:1px solid #cbd5e1;border-radius:8px;height:34mm;background:repeating-linear-gradient(#fff,#fff 8mm,#e2e8f0 8mm,#e2e8f0 8.3mm)}
.sign{display:flex;gap:10px;margin-top:10px;break-inside:avoid}
.sign div{flex:1;border:1px solid #cbd5e1;border-radius:8px;height:24mm;padding:5px 9px;font-size:9.5pt;color:#64748b}
.gen{margin-top:14px;font-size:8.5pt;color:#94a3b8}
@media screen{body{max-width:190mm;margin:10px auto;padding:0 8px}}
"""


@lru_cache(maxsize=1)
def _logo_uri() -> str:
    """شعار أسوار بلس مضمّناً في الصفحة (يعمل في المتصفح وفي WeasyPrint دون طلب خارجي)."""
    try:
        return "data:image/png;base64," + base64.b64encode((Path(__file__).parent / "assets" / "logo-pdf.png").read_bytes()).decode()
    except OSError:
        return ""


def _t(v) -> str:
    return _e("" if v is None else str(v))


def _val_html(f: dict, v) -> str:
    if f["type"] == "checkbox":
        return "نعم" if v else "لا"
    if v is None or v == "":
        return "—"
    return _t(v)


def _body_html(schema: dict, body: dict) -> str:
    out = []
    for f in schema.get("fields", []):
        v = body.get(f["key"])
        if f["type"] == "table":
            rows = v if isinstance(v, list) else []
            if not rows:
                out.append(f'<div class="fld"><div class="k">{_t(f["label"])}</div><div class="v">—</div></div>')
                continue
            head = "".join(f"<th>{_t(c['label'])}</th>" for c in f["columns"])
            trs = "".join("<tr>" + "".join(f"<td>{_t(r.get(c['key']))}</td>" for c in f["columns"]) + "</tr>" for r in rows)
            out.append(f'<div class="fld"><div class="k">{_t(f["label"])}</div>'
                       f'<table class="data"><thead><tr>{head}</tr></thead><tbody>{trs}</tbody></table></div>')
        else:
            out.append(f'<div class="fld"><div class="k">{_t(f["label"])}</div><div class="v">{_val_html(f, v)}</div></div>')
    return "".join(out) or '<p>لا توجد حقول.</p>'


def _media_html(media: list[dict]) -> str:
    if not media:
        return ""
    imgs = [m for m in media if m.get("data_uri")]
    others = [m for m in media if not m.get("data_uri")]
    parts = [f"<h2>المرفقات ({len(media)})</h2>"]
    if imgs:
        figs = []
        for m in imgs:
            tag = f'<span class="tag" style="background:{_t(m.get("color") or "#0f766e")}">{_t(m.get("category"))}</span> ' if m.get("category") else ""
            cap = _t(m.get("caption") or m.get("original_name") or "")
            figs.append(f'<figure><img src="{m["data_uri"]}" alt=""><figcaption>{tag}{cap}</figcaption></figure>')
        parts.append('<div class="gallery">' + "".join(figs) + "</div>")
    if others:
        li = "".join(f'<li>{"🎬" if m.get("kind") == "video" else "🖼️"} {_t(m.get("caption") or m.get("original_name") or "ملف")}'
                     f' <span style="color:#64748b">({_t(m.get("category"))}{" · " + _t(m["size"]) if m.get("size") else ""})</span></li>' for m in others)
        parts.append(f'<p style="margin:8px 0 0;font-size:10pt;color:#475569">مرفقات لا تُدرج داخل الملف (فيديو أو صور كثيرة)، وتجدها في مكتبة الوسائط:</p><ul class="vids">{li}</ul>')
    return "".join(parts)


def render_report_html(rep: dict, media: list[dict], company: dict, generated_at: str, font_base: str = "/fonts/") -> str:
    serial = rep.get("serial") or ""
    status = rep.get("status", "")
    draft = status == "draft"
    css = CSS.replace("__FB__", font_base).replace("__SERIAL__", serial or "مسودة")
    meta = [
        ("الرقم المسلسل", serial or "— (مسودة)"), ("النوع", rep.get("category")),
        ("التاريخ", rep.get("report_date")), ("الوقت", rep.get("time")),
        ("الكاتب", rep.get("author")), ("الموقع", rep.get("site")),
        ("القطاع / الإدارة", rep.get("department")), ("الأولوية", PRIORITY_LABELS.get(rep.get("priority"), rep.get("priority"))),
        ("الحالة", STATUS_LABELS.get(status, status)), ("إصدار القالب", f"v{rep.get('template_version', 1)}"),
    ]
    rows = "".join(f'<tr><td class="k">{_t(meta[i][0])}</td><td class="v">{_t(meta[i][1]) or "—"}</td>'
                   f'<td class="k">{_t(meta[i + 1][0])}</td><td class="v">{_t(meta[i + 1][1]) or "—"}</td></tr>'
                   for i in range(0, len(meta), 2))
    logo = _logo_uri()
    co_html = (f'<div class="co"><img class="logo" src="{logo}" alt="{_t(company.get("name"))}"></div>' if logo else
               f'<div class="co"><b>{_t(company.get("name"))}</b><span>{_t(company.get("subtitle"))}</span></div>')
    banner = '<div class="banner">مسودة غير معتمدة: لم يُمنح التقرير رقماً مسلسلاً بعد ولا يُعدّ نسخة رسمية.</div>' if draft else ""
    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_t(serial or "مسودة")} — {_t(rep.get("title"))}</title><style>{css}</style></head><body>
<div class="head">{co_html}
<div class="doc"><small>{_t(rep.get("category"))}</small><strong>{_t(serial or "DRAFT")}</strong></div></div>
{banner}
<h1>{_t(rep.get("title"))}</h1>
<table class="meta">{rows}</table>
<h2>تفاصيل التقرير</h2>
{_body_html(rep.get("schema") or {}, rep.get("body") or {})}
{_media_html(media)}
<h2>ملاحظات الإدارة</h2>
<div class="notes"></div>
<div class="sign"><div>اسم المطّلع</div><div>التاريخ</div><div>التوقيع «تم الاطلاع»</div></div>
<div class="gen">أُنشئ هذا الملف من منظومة أسوار بلس للحصر والإشغال في {_t(generated_at)}</div>
</body></html>"""


def pdf_from_html(html: str, base_url: str | None = None) -> bytes:
    """يحوّل HTML إلى PDF بـ WeasyPrint (مثبّت في صورة Docker). يرفع RuntimeError إن لم يتوفر."""
    try:
        from weasyprint import HTML
    except Exception as e:                    # noqa: BLE001 — مكتبات النظام قد تغيب
        raise RuntimeError("WeasyPrint غير متوفر") from e
    return HTML(string=html, base_url=base_url).write_pdf()
