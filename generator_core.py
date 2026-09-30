                    "scenesrc": {
                        "path": rel_path,
                        "mime": mime,
                        "copyright": {"license": "U"},
                    },
                    "scenename": title,
                    "scenedescription": "",
                    "cameraStartPosition": "0,0",
                    "interactions": interactions,
                }
            ],
            "startSceneId": 0,
        },
        "behaviour": {
            "sceneRenderingQuality": "high",
            "label": {
                "showLabel": True,
                "labelPosition": "right",
            },
        },
    }
 
    _save_json(work_dir, "content/content.json", content)
 
    # ── Build QA items ────────────────────────────────────────────────────
    qa_items = [{
        "label": "360 Scene",
        "content": f"Single 360° scene: {title} ({rel_path})",
        "evidence": {},
    }]
 
    for i, hs in enumerate(hotspots, start=1):
        ev = hs.get("evidence") or {}
        label = (hs.get("label") or f"Hotspot {i}").strip()
        body_plain = re.sub(r"<[^>]+>", "", hs.get("body_html") or "")
        qa_items.append({
            "label": f"Hotspot {i}: {label}",
            "content": body_plain[:400],
            "expected": "",
            "evidence": ev,
        })
 
    return qa_items

def write_qa_report_html(path: str, title: str, activity_type: str, qa_items: List[Dict[str, Any]]) -> None:
    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def tokens(s: str) -> List[str]:
        s = (s or "").lower()
        s = re.sub(r"[^a-z0-9\s]", " ", s)
        t = [w for w in s.split() if len(w) >= 4]
        stop = {"this","that","with","from","into","your","have","will","must","should","also","such","than","then","when","where","which","what","over","under","between","within","about"}
        return [w for w in t if w not in stop]

    def content_supported(content: str, quote: str) -> bool:
        a = set(tokens(content))
        b = set(tokens(quote))
        if not a or not b:
            return False
        overlap = len(a & b)
        return overlap >= max(2, int(0.25 * min(len(a), len(b))))

    def expected_in_quote(expected: str, quote: str) -> bool:
        exp = (expected or "").strip()
        if not exp:
            return False
        return re.search(r"\b" + re.escape(exp) + r"\b", quote or "", re.IGNORECASE) is not None

    def item_status(it: Dict[str, Any]) -> str:
        ev = it.get("evidence", {}) or {}
        quote = (ev.get("quote") or "").strip()
        expected = (it.get("expected") or "").strip()
        content = (it.get("content") or "").strip()
        # For True/False items the expected answer is the boolean word
        # ("True"/"False"), which never appears literally in the source PDF.
        # Checking the literal word against the quote would always fail, so
        # fall back to verifying that the statement content is supported by
        # the quote instead.
        if expected and expected.lower() in ("true", "false"):
            return "Match" if content_supported(content, quote) else "Needs review"
        if expected:
            return "Match" if expected_in_quote(expected, quote) else "No match"
        return "Match" if content_supported(content, quote) else "Needs review"

    statuses = [item_status(it) for it in qa_items]
    total = len(statuses)
    match_count = sum(1 for s in statuses if s == "Match")
    no_match_count = sum(1 for s in statuses if s == "No match")
    review_count = sum(1 for s in statuses if s == "Needs review")
    overall = "Match" if (total > 0 and match_count == total) else "Not fully matched"

    rows = []
    for it, stt in zip(qa_items, statuses):
        ev = it.get("evidence", {}) or {}
        expected = (it.get("expected") or "").strip()
        quote = ev.get("quote", "") or ""
        color = "#0a7d24" if stt.startswith(("Verified", "Match")) else ("#b00020" if stt in ("Quote not found in PDF", "Answer not in quote") else "#a06000")
        rows.append(
            f"<div style='padding:12px;border:1px solid #e7e7e7;border-radius:10px;margin:10px 0;'>"
            f"<div style='font-weight:600'>{esc(it.get('label','Item'))}</div>"
            f"<div style='margin-top:6px'><b>Item:</b> {esc(it.get('content',''))}</div>"
            + (f"<div style='margin-top:6px'><b>Expected answer:</b> {esc(expected)}</div>" if expected else "")
            + f"<div style='margin-top:6px'><b>Source in PDF:</b> {esc(ev.get('source_file',''))} — {esc(ev.get('locator',''))}</div>"
            + f"<div style='margin-top:6px'><b>Relevant text (PDF):</b> <i>{esc(quote)}</i></div>"
            + f"<div style='margin-top:6px'><b>Status:</b> <span style='color:{color};font-weight:600'>{esc(stt)}</span></div>"
            + f"</div>"
        )

    html = f"""<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)} - QA</title></head>
<body style='font-family:Arial, sans-serif;max-width:960px;margin:24px auto;'>
<h2 style='margin-bottom:4px'>{esc(title)}</h2>
<div style='color:#666;margin-bottom:16px'>
  <div><b>Type:</b> {esc(activity_type)}</div>
</div>

<div style='padding:12px;border:1px solid #d7d7d7;border-radius:10px;background:#fafafa;margin:14px 0;'>
  <div style='font-weight:700'>Overall report</div>
  <div style='margin-top:6px'><b>Overall status:</b> {esc(overall)}</div>
  <div style='margin-top:6px'><b>Total items:</b> {total}</div>
  <div style='margin-top:6px'><b>Verified:</b> {match_count} &nbsp;&nbsp; <b>Failed verification:</b> {no_match_count} &nbsp;&nbsp; <b>Needs review:</b> {review_count}</div>
</div>

<p><b>Evidence per item (verified against the extracted PDF text):</b></p>
{''.join(rows) if rows else '<p>No QA items.</p>'}
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
