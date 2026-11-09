import { Literata } from "next/font/google";

/** Shared long-form reading face for BD source pages and DD synthesized chapters. */
export const readerFont = Literata({
  axes: ["opsz"],
  display: "swap",
  fallback: ["Georgia", "serif"],
  subsets: ["latin"],
});
