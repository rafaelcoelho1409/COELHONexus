import { Literata } from "next/font/google";

/** Shared long-form reading face for Book Distiller study content. */
export const readerFont = Literata({
  axes: ["opsz"],
  display: "swap",
  fallback: ["Georgia", "serif"],
  subsets: ["latin"],
});
