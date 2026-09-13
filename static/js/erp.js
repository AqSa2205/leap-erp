/*
 * Shared browser helpers for the Leap ERP.
 *
 * Every page in the app posts something back — a grid cell, a toggle, a
 * status change — and until this file existed each template solved the same
 * three problems on its own: find the CSRF token, set the right headers, and
 * turn a mixed success/error response into something to act on. Fourteen
 * templates had done it, in three different ways, and base.html's own copies
 * of `csrfToken` and `getCookie` were declared inside a function, so nothing
 * else could reach them.
 *
 * Kept deliberately small. This is a place for the things every page needs,
 * not a framework: two functions and no dependencies, loaded from base.html
 * so it is available before any page's own script runs.
 */
(function (window, document) {
  'use strict';

  function cookie(name) {
    var match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return match ? match.pop() : '';
  }

  /*
   * The CSRF token for this page.
   *
   * Tried in the order the app actually provides it. A rendered form's hidden
   * input is the most reliable — it is present even when the cookie is
   * HttpOnly — so it is preferred, with the cookie as the fallback for pages
   * that post without carrying a form.
   */
  function csrf() {
    var field = document.querySelector('input[name=csrfmiddlewaretoken]');
    if (field && field.value) {
      return field.value;
    }
    return cookie('csrftoken');
  }

  /*
   * POST form-encoded data and hand back both the HTTP outcome and the parsed
   * body.
   *
   * Resolves rather than rejects on a 4xx: the app's endpoints answer failure
   * with a JSON `error` message meant to be shown to the user, and a rejected
   * promise loses it. Callers get {ok, status, payload} and decide.
   *
   * Rejects only when the request could not be completed or the response was
   * not JSON at all — which is a different problem and deserves a different
   * message.
   */
  function postForm(url, data) {
    var body = new URLSearchParams();
    Object.keys(data || {}).forEach(function (key) {
      var value = data[key];
      body.append(key, value === null || value === undefined ? '' : value);
    });

    return fetch(url, {
      method: 'POST',
      headers: {
        'X-CSRFToken': csrf(),
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: body.toString(),
    }).then(function (response) {
      return response.json().then(function (payload) {
        return { ok: response.ok && !payload.error, status: response.status, payload: payload };
      }, function () {
        // A non-JSON body means something upstream failed — a login redirect,
        // a 500 page. Saying so beats reporting "undefined".
        throw new Error('The server returned an unexpected response (' + response.status + ').');
      });
    });
  }

  window.ERP = window.ERP || {};
  window.ERP.csrf = csrf;
  window.ERP.cookie = cookie;
  window.ERP.postForm = postForm;
})(window, document);
