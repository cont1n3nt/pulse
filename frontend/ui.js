const layout = document.querySelector(".layout");
const sidebar = document.querySelector("#sidebar-block");
const results = document.querySelector("#results-block");
const DURATION = 500;

sidebar.querySelector("#sidebar-toggle").addEventListener("click", (event) => {
  event.stopPropagation();
  sidebar.classList.add("collapsed");
  sidebar.title = "Развернуть историю поездок";
});

sidebar.addEventListener("click", () => {
  if (!sidebar.classList.contains("collapsed")) return;
  sidebar.classList.remove("collapsed");
  sidebar.removeAttribute("title");
});

let resultsBusy = false;

function animateResults(collapse) {
  if (resultsBusy || results.classList.contains("collapsed") === collapse) return;
  resultsBusy = true;

  const columns = () => getComputedStyle(layout).gridTemplateColumns;
  layout.style.transition = "none";
  const from = columns();
  results.classList.toggle("collapsed", collapse);
  const to = columns();

  if (from === to) {
    layout.style.transition = "";
    resultsBusy = false;
    return;
  }

  const finalWidth = parseFloat(to.split(" ")[2]) || 0;
  results.classList.remove("collapsed");
  results.style.setProperty("--frozen-w", `${collapse ? results.offsetWidth : finalWidth}px`);
  results.classList.add("animating");

  layout.style.gridTemplateColumns = from;
  layout.offsetWidth;
  layout.style.transition = "";
  layout.style.gridTemplateColumns = to;

  setTimeout(() => {
    results.classList.toggle("collapsed", collapse);
    results.classList.remove("animating");
    layout.style.gridTemplateColumns = "";
    resultsBusy = false;
  }, DURATION);
}

document.querySelector("#results-toggle").addEventListener("click", () => {
  animateResults(true);
});

document.querySelectorAll(".home-btn").forEach((button) => {
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    animateResults(!results.classList.contains("collapsed"));
  });
});

matchMedia("(max-width: 650px)").addEventListener("change", (event) => {
  if (!event.matches) return;
  sidebar.classList.remove("collapsed");
  results.classList.remove("collapsed");
});
