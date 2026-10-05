// Marks the live status block as stale when polling fails, so an old "Online" is never trusted.
// htmx keeps the last good fragment on screen when a request fails; this makes that visible.
(function () {
  function markStale() {
    var status = document.getElementById("status");
    if (status) {
      status.setAttribute("data-stale", "true");
    }
  }

  ["htmx:sendError", "htmx:responseError", "htmx:timeout"].forEach(function (name) {
    document.addEventListener(name, markStale);
  });
})();
