import { Atkinson_Hyperlegible_Next } from "next/font/google";

/** Technical prose for DD Study; code blocks keep the application's JetBrains Mono face. */
export const ddStudyFont = Atkinson_Hyperlegible_Next({
  display: "swap",
  fallback: ["Arial", "sans-serif"],
  style: ["normal", "italic"],
  subsets: ["latin"],
});
