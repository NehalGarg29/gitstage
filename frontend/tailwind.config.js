/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        dark: {
          900: "#0B0F19",
          800: "#111827",
          700: "#1F2937",
          600: "#374151"
        }
      }
    },
  },
  plugins: [],
}
