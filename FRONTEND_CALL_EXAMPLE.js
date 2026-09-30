async function publishToH5P(jobId) {
  const api = process.env.NEXT_PUBLIC_API_URL;
  const response = await fetch(`${api}/api/jobs/${jobId}/publish-to-h5p`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      auto_save: true,
      inspect_test_target: true,
    }),
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.detail || "H5P.com publish failed");
  }

  if (data.url) {
    window.open(data.url, "_blank", "noopener,noreferrer");
  }
  return data;
}
