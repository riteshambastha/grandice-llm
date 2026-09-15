const navToggle = document.querySelector("[data-nav-toggle]");
const nav = document.querySelector("[data-nav]");

if (navToggle && nav) {
  navToggle.addEventListener("click", () => {
    const open = navToggle.getAttribute("aria-expanded") === "true";
    navToggle.setAttribute("aria-expanded", String(!open));
    nav.classList.toggle("open", !open);
  });

  nav.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => {
      navToggle.setAttribute("aria-expanded", "false");
      nav.classList.remove("open");
    });
  });
}

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    await navigator.clipboard.writeText(button.dataset.copy);
    const original = button.textContent;
    button.textContent = "Copied";
    window.setTimeout(() => {
      button.textContent = original;
    }, 1600);
  });
});

document.querySelectorAll(".copy-code").forEach((button) => {
  button.addEventListener("click", async () => {
    const container = button.closest(".code-block, .mini-code, .standalone-code");
    const code = container?.querySelector("code");
    if (!code) return;

    await navigator.clipboard.writeText(code.textContent.trim());
    const original = button.textContent;
    button.textContent = "Copied";
    window.setTimeout(() => {
      button.textContent = original;
    }, 1600);
  });
});

document.querySelectorAll(".code-tabs").forEach((tabGroup) => {
  const panel = tabGroup.closest(".code-panel");
  tabGroup.querySelectorAll("[data-code-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      const target = tab.dataset.codeTab;
      tabGroup.querySelectorAll("[data-code-tab]").forEach((item) => {
        item.classList.toggle("active", item === tab);
      });
      panel.querySelectorAll("[data-code-panel]").forEach((item) => {
        item.classList.toggle("active", item.dataset.codePanel === target);
      });
    });
  });
});

const contactForm = document.querySelector("[data-contact-form]");
if (contactForm) {
  const status = contactForm.querySelector("[data-contact-status]");
  const submitButton = contactForm.querySelector("button[type='submit']");
  const submitLabel = contactForm.querySelector("[data-submit-label]");

  contactForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.className = "form-status";
    status.textContent = "";

    if (!contactForm.reportValidity()) return;

    const formData = new FormData(contactForm);
    const turnstileToken = formData.get("cf-turnstile-response");
    if (!turnstileToken) {
      status.className = "form-status error";
      status.textContent = "Please complete the human verification.";
      return;
    }

    submitButton.disabled = true;
    submitLabel.textContent = "Sending…";

    try {
      const response = await fetch("/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: formData.get("name"),
          email: formData.get("email"),
          question: formData.get("question"),
          website: formData.get("website"),
          turnstile_token: turnstileToken,
        }),
      });
      const responseText = await response.text();
      let result = {};
      try {
        result = JSON.parse(responseText);
      } catch {
        result = {};
      }
      if (!response.ok) {
        const detail = Array.isArray(result.detail)
          ? result.detail.map((item) => item.msg).join(" ")
          : result.detail;
        throw new Error(detail || "Your message could not be sent.");
      }
      window.location.assign("/contact/thanks");
    } catch (error) {
      status.className = "form-status error";
      status.textContent = error.message || "Your message could not be sent. Please try again.";
      window.turnstile?.reset();
    } finally {
      submitButton.disabled = false;
      submitLabel.textContent = "Send message";
    }
  });
}

const docsNav = document.querySelector("[data-docs-nav]");
if (docsNav && "IntersectionObserver" in window) {
  const links = new Map(
    [...docsNav.querySelectorAll("a[href^='#']")].map((link) => [
      link.getAttribute("href").slice(1),
      link,
    ]),
  );

  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;

      links.forEach((link, id) => {
        link.classList.toggle("active", id === visible.target.id);
      });
    },
    { rootMargin: "-18% 0px -68% 0px", threshold: [0, 0.2, 0.5] },
  );

  document.querySelectorAll(".docs-anchor").forEach((section) => observer.observe(section));
}
