document.addEventListener("DOMContentLoaded", function() {
  var holder = document.querySelector("[data-month-picker]");
  if (!holder) return;
  var months = window.MONTHS_INDEX || [];
  if (!months.length) return;
  var current = holder.getAttribute("data-current");
  var sel = document.createElement("select");
  sel.className = "month-nav-select";
  sel.setAttribute("aria-label", "Jump to month");
  for (var i = 0; i < months.length; i++) {
    var m = months[i];
    var opt = document.createElement("option");
    opt.value = "../month/" + m.ym + ".html";
    opt.textContent = m.label + " \u2014 " + m.count + " tweet" + (m.count === 1 ? "" : "s");
    if (m.ym === current) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", function() {
    if (sel.value) window.location.href = sel.value;
  });
  holder.appendChild(sel);
});
