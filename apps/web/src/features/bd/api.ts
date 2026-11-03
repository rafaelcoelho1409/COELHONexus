/** Book Distiller — the calls the generated client cannot make: the SSE address, figure addresses (an `<img src>`),
 * and the upload, which goes through `XMLHttpRequest` because `fetch` reports no upload progress. Everything else
 * uses the generated client from `@/shared/api` (see `hooks/use-bd-data.ts`). */

import { client } from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";

import { type StartResponse, startResponseSchema } from "./entities";
import { MAX_UPLOAD_LABEL } from "./params";

/** SSE address of one book's extraction. Streams are not typed in the spec; `client.buildUrl` keeps the base URL. */
export function eventsUrl(bookId: string): string {
  return client.buildUrl({ url: "/api/v1/bd/ingestion/{book_id}/events", path: { book_id: bookId } });
}

/** A figure cropped from a page, as PNG. */
export function figureUrl(bookId: string, name: string): string {
  return client.buildUrl({
    url: "/api/v1/bd/ingestion/{book_id}/figures/{name}",
    path: { book_id: bookId, name },
  });
}

class UploadError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "UploadError";
    this.status = status;
  }
}

/** One readable line for a failed upload (or any other failed call). */
export function describeUploadError(error: unknown): string {
  return error instanceof UploadError ? error.message : apiErrorMessage(error);
}

function failure(status: number, text: string): UploadError {
  if (status === 413) return new UploadError(status, `That file is over ${MAX_UPLOAD_LABEL}.`);
  try {
    const body: unknown = JSON.parse(text);
    const line = apiErrorMessage(body);
    return new UploadError(status, line);
  } catch {
    return new UploadError(
      status,
      status === 0 ? "The upload could not reach the server." : `The upload failed (${status}).`,
    );
  }
}

/** Uploads a PDF or EPUB (multipart, field `file`). `onProgress` gets the fraction sent, 0 to 1. The same file is the same
 * book: the answer says whether the extraction was queued, is already cached, or is already running. */
export function uploadBook(
  file: File,
  { onProgress }: { onProgress?: (fraction: number) => void } = {},
): Promise<StartResponse> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", client.buildUrl({ url: "/api/v1/bd/ingestion" }));
    request.responseType = "text";
    request.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(event.loaded / event.total);
    };
    request.onerror = () => reject(failure(0, ""));
    request.onload = () => {
      if (request.status < 200 || request.status >= 300) {
        reject(failure(request.status, request.responseText));
        return;
      }
      try {
        resolve(startResponseSchema.parse(JSON.parse(request.responseText)));
      } catch {
        reject(new UploadError(request.status, "The server's answer was not what the page expected."));
      }
    };
    const form = new FormData();
    form.append("file", file);
    request.send(form);
  });
}
