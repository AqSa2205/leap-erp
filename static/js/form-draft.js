function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(';').shift();
}

function initFormDraft(formEl, formKey, objectId) {
  let timer;
  const csrftoken = getCookie('csrftoken');

  function collect() {
    const data = {};
    const fd = new FormData(formEl);
    for (const [key, value] of fd.entries()) {
      if (key === 'csrfmiddlewaretoken') continue;
      if (Object.prototype.hasOwnProperty.call(data, key)) {
        if (Array.isArray(data[key])) {
          data[key].push(value);
        } else {
          data[key] = [data[key], value];
        }
      } else {
        data[key] = value;
      }
    }
    return data;
  }

  function saveDraft() {
    fetch('/drafts/save/', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrftoken},
      body: JSON.stringify({form_key: formKey, object_id: objectId, data: collect()})
    });
  }

  formEl.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(saveDraft, 1500);
  });

  window.addEventListener('pagehide', function () {
    navigator.sendBeacon(
      '/drafts/save/',
      new Blob([JSON.stringify({form_key: formKey, object_id: objectId, data: collect()})],
                {type: 'application/json'})
    );
  });
}

function applyDraftData(formEl, data, skipKeys) {
  skipKeys = skipKeys || [];
  Object.keys(data).forEach(function (key) {
    if (skipKeys.indexOf(key) !== -1) return;
    var value = data[key];
    var nodes = formEl.querySelectorAll('[name="' + key + '"]');
    if (nodes.length === 0) return;
    if (nodes[0].type === 'checkbox' || nodes[0].type === 'radio') {
      var values = Array.isArray(value) ? value : [value];
      nodes.forEach(function (node) {
        node.checked = values.indexOf(node.value) !== -1;
      });
    } else {
      nodes[0].value = value;
    }
  });
}

function getFieldValue(formEl, key) {
  var nodes = formEl.querySelectorAll('[name="' + key + '"]');
  if (nodes.length === 0) return undefined;
  if (nodes[0].type === 'checkbox' || nodes[0].type === 'radio') {
    var checked = [];
    nodes.forEach(function (node) { if (node.checked) checked.push(node.value); });
    return checked;
  }
  return nodes[0].value;
}

function valuesEqual(a, b) {
  var aa = Array.isArray(a) ? a.slice().sort() : [a];
  var bb = Array.isArray(b) ? b.slice().sort() : [b];
  return aa.length === bb.length && aa.join('|') === bb.join('|');
}

function clearUnchangedDraftFields(formEl, snapshot) {
  Object.keys(snapshot).forEach(function (key) {
    var current = getFieldValue(formEl, key);
    if (valuesEqual(current, snapshot[key])) {
      var nodes = formEl.querySelectorAll('[name="' + key + '"]');
      nodes.forEach(function (node) {
        if (node.type === 'checkbox' || node.type === 'radio') {
          node.checked = false;
        } else {
          node.value = '';
        }
      });
    }
  });
}