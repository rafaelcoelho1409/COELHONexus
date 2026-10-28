/** Fixtures for YCS tests — parsed through the real zod schema so the shapes stay honest. */

import type { VideoSnippet } from "./entities";
import { videoSnippetSchema } from "./entities";

export function snippet(over: Partial<VideoSnippet> & { id: string }): VideoSnippet {
  return videoSnippetSchema.parse({ kind: "video", url: `https://youtube.com/watch?v=${over.id}`, ...over });
}
