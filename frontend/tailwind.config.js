/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#12151C", panel: "#1B2029", line: "#2A3040" },
        parchment: { DEFAULT: "#F6F1E7", dim: "#EAE3D2", ink: "#33302A" },
        brass: { DEFAULT: "#C9A227", bright: "#E0BC4A", dim: "#8A7220" },
        signal: { good: "#4E9F6E", bad: "#C1553D" },
        mute: "#8B93A3",
      },
      fontFamily: {
        display: ["Fraunces", "serif"],
        sans: ["Inter", "ui-sans-serif", "system-ui"],
      },
      keyframes: {
        flash: {
          "0%": { backgroundColor: "rgba(201,162,39,0.35)" },
          "100%": { backgroundColor: "transparent" },
        },
        pulseSoft: {
          "0%, 100%": { transform: "scale(1)", opacity: "0.9" },
          "50%": { transform: "scale(1.06)", opacity: "1" },
        },
      },
      animation: {
        flash: "flash 1.1s ease-out",
        pulseSoft: "pulseSoft 1.8s ease-in-out infinite",
      },
    },
  },
  plugins: [],
}
