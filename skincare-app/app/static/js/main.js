document.addEventListener("DOMContentLoaded", () => {
  // Welcome page: login / register tab switch
  const tabButtons = document.querySelectorAll("[data-tab-target]");
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.getAttribute("data-tab-target");

      document.querySelectorAll(".tab-switch button").forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      document.querySelectorAll(".form-panel").forEach((panel) =>
        panel.classList.toggle("active", panel.id === target)
      );
    });
  });

  // Auto-uppercase invitation code fields as the model types
  document.querySelectorAll("input[name='invitation_code']").forEach((input) => {
    input.addEventListener("input", () => {
      input.value = input.value.toUpperCase();
    });
  });

  // Fade out flash messages after a few seconds
  document.querySelectorAll(".flash").forEach((el, i) => {
    setTimeout(() => {
      el.style.transition = "opacity 0.4s ease";
      el.style.opacity = "0";
    }, 4500 + i * 200);
  });
});
