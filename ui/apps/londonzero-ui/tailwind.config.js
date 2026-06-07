/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./pages/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: "#76b900", // NVIDIA green
        ink: "#0a1410", // page background (very dark green-charcoal)
        panel: "#111d18", // card surface
        panel2: "#16241d", // raised surface / hover
        edge: "#23352b", // hairline borders
        risk: {
          high: "#ef4444",
          med: "#f59e0b",
          low: "#76b900",
        },
      },
    },
  },
  plugins: [],
};
