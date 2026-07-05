(function () {
  try {
    var theme = localStorage.getItem("lattice.theme");
    document.documentElement.dataset.theme = theme === "light" || theme === "dark" ? theme : "light";
  } catch (_error) {
    document.documentElement.dataset.theme = "light";
  }
})();
