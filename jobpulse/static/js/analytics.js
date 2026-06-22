// Analytics charts. Reads window.JOBPULSE_ANALYTICS (embedded by the server)
// and renders Chart.js visualizations. Each chart guards against empty data.

(function () {
  const data = window.JOBPULSE_ANALYTICS;
  if (!data || typeof Chart === "undefined") return;

  const BLUE = "#4f46e5";
  const PALETTE = ["#4f46e5", "#15a34a", "#b4630a", "#dc2626", "#7c6cf6", "#0891b2", "#db2777", "#0d9488"];
  const FONT = "Inter, system-ui, sans-serif";
  Chart.defaults.font.family = FONT;
  Chart.defaults.color = "#7c8598";
  const baseOpts = { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } };

  function make(id, config) {
    const el = document.getElementById(id);
    if (el) new Chart(el, config);
  }

  // Applications per day (bar) — oldest → newest left to right.
  const apd = (data.applications_per_day || []).slice().reverse();
  make("chart-apps-day", {
    type: "bar",
    data: {
      labels: apd.map((d) => d.day),
      datasets: [{ data: apd.map((d) => d.count), backgroundColor: BLUE }],
    },
    options: { ...baseOpts, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
  });

  // Status funnel (horizontal bar).
  const funnel = data.status_funnel || [];
  make("chart-funnel", {
    type: "bar",
    data: {
      labels: funnel.map((s) => s.status),
      datasets: [{ data: funnel.map((s) => s.count), backgroundColor: PALETTE }],
    },
    options: { ...baseOpts, indexAxis: "y", scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } },
  });

  // Jobs by ATS platform (donut).
  const ats = (data.ats_breakdown && data.ats_breakdown.jobs) || [];
  make("chart-ats", {
    type: "doughnut",
    data: {
      labels: ats.map((a) => a.ats_type),
      datasets: [{ data: ats.map((a) => a.count), backgroundColor: PALETTE }],
    },
    options: { ...baseOpts, plugins: { legend: { display: true, position: "right" } } },
  });

  // Scrape trend (line) — jobs inserted per day.
  const trend = data.scrape_trends || [];
  make("chart-scrape", {
    type: "line",
    data: {
      labels: trend.map((t) => t.day),
      datasets: [{ data: trend.map((t) => t.inserted), borderColor: BLUE, backgroundColor: "rgba(79,70,229,.12)", fill: true, tension: 0.25, pointRadius: 3, pointBackgroundColor: BLUE }],
    },
    options: { ...baseOpts, scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
  });
})();
