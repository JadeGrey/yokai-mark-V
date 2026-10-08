// Two small jobs for the HTMX pages.
// 1. When polling fails, mark the live status block stale so an old "Online" is never trusted.
// 2. When any other HTMX request fails (a server error, a lost connection, a timeout), show the
//    request-failed notice: HTMX itself swaps nothing on an error, so without this a failed
//    action would look like nothing happened.
(function () {
  function markStale() {
    var status = document.getElementById("status");
    if (status) {
      status.setAttribute("data-stale", "true");
    }
  }

  function showRequestFailed(event) {
    var status = document.getElementById("status");
    var source = event.target;
    if (status && source && (source === status || status.contains(source))) {
      return; // the status block handles its own polling failures
    }
    var notice = document.getElementById("request-error");
    if (notice) {
      notice.hidden = false;
    }
  }

  ["htmx:sendError", "htmx:responseError", "htmx:timeout"].forEach(function (name) {
    document.addEventListener(name, markStale);
    document.addEventListener(name, showRequestFailed);
  });
})();
