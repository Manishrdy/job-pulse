// JobPulse front-end glue. HTMX handles filter submission and card actions
// declaratively; this file adds only small enhancements.

document.addEventListener("DOMContentLoaded", () => {
  // Surface any HTMX request error to the user instead of failing silently.
  document.body.addEventListener("htmx:responseError", (evt) => {
    console.error("Action failed:", evt.detail);
    alert("Something went wrong with that action. Please retry.");
  });
});
