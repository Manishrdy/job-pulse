// JobPulse front-end glue. HTMX handles filter submission and card actions
// declaratively; this file adds only small enhancements.

document.addEventListener("DOMContentLoaded", () => {
  // Highlight the active nav item based on the current path.
  const path = window.location.pathname;
  document.querySelectorAll(".nav a").forEach((a) => {
    const href = a.getAttribute("href");
    const isActive = href === "/" ? path === "/" : path.startsWith(href);
    if (isActive) a.classList.add("active");
  });

  // Surface any HTMX request error to the user instead of failing silently.
  document.body.addEventListener("htmx:responseError", (evt) => {
    console.error("Action failed:", evt.detail);
    alert("Something went wrong with that action. Please retry.");
  });
});
