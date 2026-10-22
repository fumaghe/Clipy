/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: { brand: { 400: "#a78bfa", 500: "#8b5cf6", 600: "#7c3aed" } },
      fontFamily: { sans: ["Inter", "system-ui", "Segoe UI", "sans-serif"] },
    },
  },
  plugins: [],
};
