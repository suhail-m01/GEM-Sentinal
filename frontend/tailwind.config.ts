import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        navy: {
          50: "#f0f5fa",
          100: "#dce7f3",
          200: "#bcd2e8",
          300: "#8fb3d8",
          400: "#5c8cc2",
          500: "#3a6ea8",
          600: "#2b578c",
          700: "#1f4470",
          800: "#143357",
          900: "#0b2545",
          950: "#071a33",
        },
        // Saffron - Indian-government accent (pairs with the navy institutional
        // palette; success states stay green).
        accent: {
          50: "#fff7ed",
          100: "#ffedd5",
          200: "#fed7aa",
          300: "#fdba74",
          400: "#fb923c",
          500: "#f97316",
          600: "#ea580c",
          700: "#c2410c",
          800: "#9a3412",
          900: "#7c2d12",
        },
        critical: {
          500: "#9d174d",
          600: "#88133f",
          700: "#701035",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
