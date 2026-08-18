export type JsonPath = Array<string | number>;

type LineIndex = Map<string, number>;

function pathKey(path: JsonPath) {
  return JSON.stringify(path);
}

function lineStartsOf(text: string) {
  const starts = [0];
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] === "\n") starts.push(index + 1);
  }
  return starts;
}

function lineAt(starts: number[], offset: number) {
  let low = 0;
  let high = starts.length - 1;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    if (starts[middle] <= offset) low = middle + 1;
    else high = middle - 1;
  }
  return Math.max(0, high);
}

/**
 * Build a path-to-line index from the same pretty JSON text rendered by the
 * preview. The parser is intentionally small: JSON.stringify always emits
 * valid JSON, so handling strings, objects, arrays, and primitive tokens is
 * sufficient and avoids ambiguous key/indent matching.
 */
function indexJsonPaths(text: string): LineIndex | null {
  const starts = lineStartsOf(text);
  const index: LineIndex = new Map();
  let cursor = 0;

  function skipWhitespace() {
    while (cursor < text.length && /\s/.test(text[cursor])) cursor += 1;
  }

  function readString() {
    if (text[cursor] !== '"') return null;
    const start = cursor;
    cursor += 1;
    while (cursor < text.length) {
      const char = text[cursor];
      if (char === "\\") {
        cursor += 2;
        continue;
      }
      cursor += 1;
      if (char === '"') break;
    }
    if (text[cursor - 1] !== '"') return null;
    try {
      return JSON.parse(text.slice(start, cursor)) as string;
    } catch {
      return null;
    }
  }

  function readPrimitive() {
    const start = cursor;
    while (cursor < text.length && !/[\s,\]}]/.test(text[cursor])) cursor += 1;
    return cursor > start;
  }

  function record(path: JsonPath, offset: number) {
    if (path.length) index.set(pathKey(path), lineAt(starts, offset));
  }

  function parseValue(path: JsonPath, recordValue: boolean): boolean {
    skipWhitespace();
    if (cursor >= text.length) return false;
    const start = cursor;
    if (recordValue) record(path, start);

    const token = text[cursor];
    if (token === '"') return readString() !== null;

    if (token === "{") {
      cursor += 1;
      skipWhitespace();
      if (text[cursor] === "}") {
        cursor += 1;
        return true;
      }
      while (cursor < text.length) {
        skipWhitespace();
        const keyOffset = cursor;
        const key = readString();
        if (key === null) return false;
        skipWhitespace();
        if (text[cursor] !== ":") return false;
        cursor += 1;
        const childPath = [...path, key];
        // Object keys are rendered on the same line as their values. Record
        // the key offset so multiline/custom formatting still points to it.
        record(childPath, keyOffset);
        if (!parseValue(childPath, false)) return false;
        skipWhitespace();
        if (text[cursor] === "}") {
          cursor += 1;
          return true;
        }
        if (text[cursor] !== ",") return false;
        cursor += 1;
      }
      return false;
    }

    if (token === "[") {
      cursor += 1;
      skipWhitespace();
      if (text[cursor] === "]") {
        cursor += 1;
        return true;
      }
      let itemIndex = 0;
      while (cursor < text.length) {
        skipWhitespace();
        const childPath = [...path, itemIndex];
        if (!parseValue(childPath, true)) return false;
        itemIndex += 1;
        skipWhitespace();
        if (text[cursor] === "]") {
          cursor += 1;
          return true;
        }
        if (text[cursor] !== ",") return false;
        cursor += 1;
      }
      return false;
    }

    return readPrimitive() || cursor > start;
  }

  skipWhitespace();
  if (!parseValue([], false)) return null;
  return index;
}

/**
 * Return the line containing the requested JSON path. If a stale path points
 * to a removed array item/property, return the nearest existing ancestor so
 * the preview still lands in the changed section. `lines` should come from
 * `JSON.stringify(value, null, 2)`.
 */
export function findLineForPath(lines: string[], path: JsonPath) {
  if (!path.length) return -1;
  const index = indexJsonPaths(lines.join("\n"));
  if (!index) return -1;
  for (let length = path.length; length > 0; length -= 1) {
    const line = index.get(pathKey(path.slice(0, length)));
    if (line !== undefined) return line;
  }
  return -1;
}
