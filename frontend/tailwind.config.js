/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Dark surface palette — neutral with a single accent.
        ink: {
          950: "#0a0a0c",
          900: "#111114",
          800: "#1a1a20",
          700: "#252530",
          600: "#3a3a48",
          500: "#5a5a6e",
        },
        accent: {
          400: "#7c9cff",
          500: "#5a7cff",
          600: "#3a5ce5",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};