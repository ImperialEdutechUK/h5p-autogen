"use client";

import { useEffect, useMemo, useState } from "react";

const API_URL = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

// FastAPI's error "detail" is usually a string, but on validation errors
// (422) it can be a list of {msg, loc} objects, or occasionally a nested
// object. Coerce any of those into a plain readable string so the UI never
// renders a bare "[object Object]".
function toErrorMessage(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === "string" ? d : d?.msg || JSON.stringify(d)))
      .join("; ");
  }
  if (typeof detail === "object") return detail.msg || JSON.stringify(detail);
  return String(detail);
}

export default function Home() {
  const [activityTypes, setActivityTypes] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [courseName, setCourseName] = useState("");
  const [unitName, setUnitName] = useState("");
  const [pdfs, setPdfs] = useState([]);
  const [qualSpec, setQualSpec] = useState(null);
  const [activityType, setActivityType] = useState("");
  const [itemCount, setItemCount] = useState(4);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const canGenerate = useMemo(
    () => API_URL && pdfs.length > 0 && courseName.trim() && unitName.trim() && activityType,
    [pdfs, courseName, unitName, activityType]
  );

  useEffect(() => {
    if (!API_URL) {
      setError("NEXT_PUBLIC_API_URL is not configured in Vercel.");
      return;
    }

    fetch(`${API_URL}/api/activity-types`)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        const list = data.recommended?.length ? data.recommended : data.all || [];
        setActivityTypes(list);
        if (list.length) setActivityType(list[0]);
      })
      .catch((e) => setError(`Could not load activity types: ${e.message}`));
  }, []);

  async function suggestTypes() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (!pdfs.length) throw new Error("Choose at least one teaching PDF.");
      if (!courseName.trim()) throw new Error("Enter the course name.");
      if (!unitName.trim()) throw new Error("Enter the unit name.");

      const fd = new FormData();
      pdfs.forEach((f) => fd.append("pdfs", f));
      fd.append("course_name", courseName.trim());
      fd.append("unit_name", unitName.trim());
      if (qualSpec) fd.append("qualification_spec", qualSpec);

      const r = await fetch(`${API_URL}/api/suggest`, { method: "POST", body: fd });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(toErrorMessage(data.detail) || "Failed to suggest H5P types.");

      const recs = data.recommendations || [];
      if (recs.length) {
        const names = recs.map((x) => x.activity_type).filter(Boolean);
        const first = recs[0] || {};
        const suggestedCount = Number(first.suggested_item_count);

        setSuggestions(recs);
        setActivityTypes(names);
        setActivityType(names[0] || "");

        if (Number.isFinite(suggestedCount) && suggestedCount > 0) {
          setItemCount(Math.min(20, Math.max(1, suggestedCount)));
        }

        setMessage(`Suggested ${names.length} H5P activity type(s). Select one below.`);
      } else {
        setSuggestions([]);
        setMessage("No recommendations returned.");
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function generateH5P() {
    setBusy(true);
    setError("");
    setMessage("");
    setResult(null);

    try {
      if (!canGenerate) throw new Error("Complete the required fields first.");

      const fd = new FormData();
      pdfs.forEach((f) => fd.append("pdfs", f));
      fd.append("course_name", courseName.trim());
      fd.append("unit_name", unitName.trim());
      fd.append("activity_type", activityType);
      fd.append("item_count", String(itemCount || 4));
      fd.append("options", "{}");
      if (qualSpec) fd.append("qualification_spec", qualSpec);

      const r = await fetch(`${API_URL}/api/generate`, { method: "POST", body: fd });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(toErrorMessage(data.detail) || "H5P generation failed.");

      setResult(data);
      setMessage("Activity generated successfully.");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function publishToH5P() {
    if (!result?.job_id) return;
    setBusy(true);
    setError("");
    setMessage("");

    try {
      const r = await fetch(`${API_URL}/api/jobs/${result.job_id}/publish-to-h5p`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auto_save: true, inspect_test_target: true }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(toErrorMessage(data.detail) || "Failed to publish to H5P.");

      const finalUrl = data.url || data.edit_url || data.content_url;
      setMessage("Published to H5P successfully.");
      if (finalUrl) window.open(finalUrl, "_blank", "noopener,noreferrer");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const abs = (path) => (path?.startsWith("http") ? path : `${API_URL}${path}`);

  return (
    <main className="page-shell">
      <section className="hero">
        <div>
          <p className="eyebrow">AI-assisted H5P builder</p>
          <h1>H5P Activity Generator</h1>
          <p className="subtitle">Upload teaching content, generate an H5P package, and publish it directly to H5P.com.</p>
        </div>
      </section>

      <section className="card">
        <h2>1. Course details</h2>
        <div className="grid two">
          <label>
            <span>Course name *</span>
            <input value={courseName} onChange={(e) => setCourseName(e.target.value)} placeholder="e.g. Level 5 Diploma in Teaching" />
          </label>
          <label>
            <span>Unit name *</span>
            <input value={unitName} onChange={(e) => setUnitName(e.target.value)} placeholder="e.g. Unit 1: Personal Development" />
          </label>
        </div>
      </section>

      <section className="card">
        <h2>2. Upload source files</h2>
        <label className="upload-box">
          <span>Teaching PDF file(s) *</span>
          <input type="file" accept="application/pdf" multiple onChange={(e) => setPdfs(Array.from(e.target.files || []))} />
          <small>{pdfs.length ? `${pdfs.length} file(s) selected` : "Select one or more text-based PDFs"}</small>
        </label>
        <label className="upload-box">
          <span>Qualification specification (optional)</span>
          <input type="file" accept="application/pdf" onChange={(e) => setQualSpec(e.target.files?.[0] || null)} />
        </label>
        <button className="secondary" onClick={suggestTypes} disabled={busy}>Suggest H5P types</button>
      </section>

      {suggestions.length > 0 && (
        <section className="card suggestions-card">
          <div className="suggestions-heading-row">
            <div>
              <p className="suggestions-kicker">AI recommendations</p>
              <h2>Suggested H5P activities</h2>
            </div>
            <span className="suggestions-count">{suggestions.length} suggested</span>
          </div>

          <p className="suggestion-intro">
            These activities were recommended from the uploaded teaching content. Select one to use it for generation.
          </p>

          <div className="suggestions-grid">
            {suggestions.map((rec, index) => {
              const isSelected = activityType === rec.activity_type;
              const templateUnavailable = rec.template_ok === false;
              const suggestedCount = Number(rec.suggested_item_count);

              return (
                <button
                  type="button"
                  key={`${rec.activity_type}-${index}`}
                  className={`suggestion-card ${isSelected ? "selected" : ""} ${templateUnavailable ? "unavailable" : ""}`}
                  disabled={busy || templateUnavailable}
                  onClick={() => {
                    setActivityType(rec.activity_type);
                    if (Number.isFinite(suggestedCount) && suggestedCount > 0) {
                      setItemCount(Math.min(20, Math.max(1, suggestedCount)));
                    }
                  }}
                  aria-pressed={isSelected}
                >
                  <div className="suggestion-top">
                    <div className="suggestion-rank">#{index + 1}</div>
                    <strong>{rec.activity_type}</strong>
                    {rec.score_0_to_5 !== undefined && rec.score_0_to_5 !== null && (
                      <span className="suggestion-score">{rec.score_0_to_5}/5</span>
                    )}
                  </div>

                  {rec.why && <p className="suggestion-reason">{rec.why}</p>}

                  <div className="suggestion-meta">
                    {Number.isFinite(suggestedCount) && suggestedCount > 0 && (
                      <span>Suggested items: {suggestedCount}</span>
                    )}
                    {templateUnavailable && <span className="template-warning">Template unavailable</span>}
                  </div>

                  {isSelected && !templateUnavailable && (
                    <span className="selected-label">Selected</span>
                  )}
                </button>
              );
            })}
          </div>
        </section>
      )}

      <section className="card">
        <h2>3. Generate activity</h2>
        <div className="grid two">
          <label>
            <span>Activity type *</span>
            <select value={activityType} onChange={(e) => setActivityType(e.target.value)}>
              {!activityTypes.length && <option value="">Choose an activity</option>}
              {activityTypes.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label>
            <span>Items / questions</span>
            <input type="number" min="1" max="20" value={itemCount} onChange={(e) => setItemCount(Number(e.target.value))} />
          </label>
        </div>
        <button className="primary" onClick={generateH5P} disabled={busy || !canGenerate}>{busy ? "Working..." : "Generate H5P"}</button>
      </section>

      {error && <div className="alert error">{error}</div>}
      {message && <div className="alert success">{message}</div>}

      {result && (
        <section className="card result-card">
          <h2>Activity ready</h2>
          <h3>{result.title}</h3>
          <div className="actions">
            <a className="primary button-link" href={abs(result.h5p_url)} target="_blank" rel="noreferrer">Download .h5p</a>
            <a className="secondary button-link" href={abs(result.qa_url)} target="_blank" rel="noreferrer">Open QA report</a>
            <button className="secondary" onClick={publishToH5P} disabled={busy}>Publish to H5P</button>
          </div>
        </section>
      )}
    </main>
  );
}
