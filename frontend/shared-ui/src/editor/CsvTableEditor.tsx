"use client";

import { Plus, Trash2 } from "lucide-react";
import { Fragment, useLayoutEffect, useMemo, useState } from "react";

export type CsvTableEditorProps = {
  documentId?: string;
  content: string;
  nodeName?: string;
  delimiter?: "," | "\t";
  readOnly?: boolean;
  onChange?: (content: string) => void;
};

type ParsedTable = {
  rows: string[][];
  warning?: string;
};

export function CsvTableEditor({
  documentId,
  content,
  nodeName = "",
  delimiter,
  readOnly = true,
  onChange,
}: CsvTableEditorProps) {
  const resolvedDelimiter = delimiter ?? inferDelimiter(nodeName, content);
  const parsed = useMemo(() => parseDelimitedText(content, resolvedDelimiter), [content, resolvedDelimiter]);
  const matrix = useMemo(() => normalizeRows(parsed.rows), [parsed.rows]);
  const [headerEnabled, setHeaderEnabled] = useState(() => inferHeaderRow(parsed.rows));
  const columnCount = Math.max(1, ...matrix.map((row) => row.length));
  const dataRows = headerEnabled ? matrix.slice(1) : matrix;
  const rowCount = content.trim() ? dataRows.length : 0;
  const gridTemplateColumns = `44px repeat(${columnCount}, minmax(128px, 1fr))`;

  useLayoutEffect(() => {
    setHeaderEnabled(inferHeaderRow(parsed.rows));
  }, [documentId]);

  const emitMatrix = (nextMatrix: string[][]) => {
    onChange?.(stringifyDelimitedText(trimRows(nextMatrix), resolvedDelimiter));
  };

  const updateCell = (rowIndex: number, columnIndex: number, value: string) => {
    const next = ensureShape(matrix, Math.max(matrix.length, rowIndex + 1), Math.max(columnCount, columnIndex + 1));
    next[rowIndex][columnIndex] = value;
    emitMatrix(next);
  };

  const addRow = () => {
    const next = ensureShape(matrix, matrix.length, columnCount);
    next.push(Array.from({ length: columnCount }, () => ""));
    emitMatrix(next);
  };

  const addColumn = () => {
    const next = ensureShape(matrix, Math.max(matrix.length, headerEnabled ? 1 : 0), columnCount + 1);
    for (const row of next) row[columnCount] = row[columnCount] ?? "";
    emitMatrix(next);
  };

  const deleteRow = (rowIndex: number) => {
    if (matrix.length <= 1) {
      emitMatrix([Array.from({ length: columnCount }, () => "")]);
      return;
    }
    emitMatrix(matrix.filter((_, index) => index !== rowIndex));
  };

  const deleteColumn = (columnIndex: number) => {
    if (columnCount <= 1) {
      emitMatrix(matrix.map(() => [""]));
      return;
    }
    emitMatrix(matrix.map((row) => row.filter((_, index) => index !== columnIndex)));
  };

  return (
    <section className="csv-table-editor" data-readonly={readOnly ? "true" : undefined}>
      <div className="csv-table-editor__toolbar">
        <div className="csv-table-editor__title">
          <strong>{nodeName || (resolvedDelimiter === "\t" ? "TSV" : "CSV")}</strong>
          <span>{rowCount} x {columnCount}</span>
        </div>
        <div className="csv-table-editor__actions">
          {parsed.warning && <span className="csv-table-editor__warning">{parsed.warning}</span>}
          <label className="csv-table-editor__header-toggle">
            <input
              type="checkbox"
              checked={headerEnabled}
              onChange={(event) => setHeaderEnabled(event.currentTarget.checked)}
            />
            <span>Header</span>
          </label>
          {!readOnly && (
            <>
              <button type="button" onClick={addRow} title="Add row" aria-label="Add row">
                <Plus size={15} />
              </button>
              <button type="button" onClick={addColumn} title="Add column" aria-label="Add column">
                <Plus size={15} />
              </button>
            </>
          )}
        </div>
      </div>

      <div className="csv-table-editor__scroll">
        <div className="csv-table-editor__grid" style={{ gridTemplateColumns }}>
          <div className="csv-table-editor__cell csv-table-editor__corner" />
          {Array.from({ length: columnCount }, (_, columnIndex) => (
            <div className="csv-table-editor__cell csv-table-editor__header-cell" key={`header-${columnIndex}`}>
              {headerEnabled ? (
                <input
                  value={matrix[0]?.[columnIndex] ?? ""}
                  readOnly={readOnly}
                  onChange={(event) => updateCell(0, columnIndex, event.currentTarget.value)}
                  aria-label={`Column ${columnIndex + 1} header`}
                  spellCheck={false}
                />
              ) : (
                <span>{toColumnLabel(columnIndex)}</span>
              )}
              {!readOnly && (
                <button
                  type="button"
                  className="csv-table-editor__cell-action"
                  onClick={() => deleteColumn(columnIndex)}
                  title="Delete column"
                  aria-label={`Delete column ${columnIndex + 1}`}
                >
                  <Trash2 size={13} />
                </button>
              )}
            </div>
          ))}

          {dataRows.length === 0 ? (
            <div className="csv-table-editor__empty" style={{ gridColumn: `1 / span ${columnCount + 1}` }}>
              {!readOnly && (
                <button type="button" onClick={addRow}>
                  <Plus size={15} />
                  <span>Add row</span>
                </button>
              )}
            </div>
          ) : (
            dataRows.map((row, visibleRowIndex) => {
              const rowIndex = headerEnabled ? visibleRowIndex + 1 : visibleRowIndex;
              return (
                <Fragment key={`row-${rowIndex}`}>
                  <div className="csv-table-editor__cell csv-table-editor__row-number">
                    <span>{visibleRowIndex + 1}</span>
                    {!readOnly && (
                      <button
                        type="button"
                        className="csv-table-editor__cell-action"
                        onClick={() => deleteRow(rowIndex)}
                        title="Delete row"
                        aria-label={`Delete row ${visibleRowIndex + 1}`}
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                  {Array.from({ length: columnCount }, (_, columnIndex) => (
                    <div className="csv-table-editor__cell csv-table-editor__body-cell" key={`cell-${rowIndex}-${columnIndex}`}>
                      <input
                        value={row[columnIndex] ?? ""}
                        readOnly={readOnly}
                        onChange={(event) => updateCell(rowIndex, columnIndex, event.currentTarget.value)}
                        aria-label={`Row ${visibleRowIndex + 1}, column ${columnIndex + 1}`}
                        spellCheck={false}
                      />
                    </div>
                  ))}
                </Fragment>
              );
            })
          )}
        </div>
      </div>
    </section>
  );
}

function inferDelimiter(name: string, content: string): "," | "\t" {
  if (name.toLowerCase().endsWith(".tsv")) return "\t";
  const sample = content.split(/\r?\n/).slice(0, 8).join("\n");
  const tabCount = (sample.match(/\t/g) ?? []).length;
  const commaCount = (sample.match(/,/g) ?? []).length;
  return tabCount > commaCount ? "\t" : ",";
}

function inferHeaderRow(rows: string[][]): boolean {
  if (rows.length < 2) return false;
  const firstRow = rows[0] ?? [];
  const secondRow = rows[1] ?? [];
  if (firstRow.length === 0 || firstRow.every((cell) => !cell.trim())) return false;
  const textLikeHeaders = firstRow.filter((cell) => cell.trim() && !looksNumeric(cell)).length;
  const numericData = secondRow.filter((cell) => looksNumeric(cell)).length;
  return textLikeHeaders >= Math.max(1, Math.ceil(firstRow.length / 2)) || numericData > 0;
}

function parseDelimitedText(content: string, delimiter: "," | "\t"): ParsedTable {
  if (!content) return { rows: [[""]] };

  const rows: string[][] = [[]];
  let current = "";
  let inQuotes = false;
  let warning: string | undefined;

  for (let index = 0; index < content.length; index += 1) {
    const char = content[index];

    if (inQuotes) {
      if (char === "\"") {
        if (content[index + 1] === "\"") {
          current += "\"";
          index += 1;
        } else {
          inQuotes = false;
        }
      } else {
        current += char;
      }
      continue;
    }

    if (char === "\"") {
      inQuotes = true;
    } else if (char === delimiter) {
      rows[rows.length - 1].push(current);
      current = "";
    } else if (char === "\n" || char === "\r") {
      rows[rows.length - 1].push(current);
      current = "";
      if (char === "\r" && content[index + 1] === "\n") index += 1;
      rows.push([]);
    } else {
      current += char;
    }
  }

  rows[rows.length - 1].push(current);

  if (inQuotes) warning = "Unclosed quote";
  if (content.endsWith("\n") || content.endsWith("\r")) {
    const lastRow = rows[rows.length - 1];
    if (lastRow.length === 1 && lastRow[0] === "") rows.pop();
  }

  return { rows: rows.length > 0 ? rows : [[""]], warning };
}

function stringifyDelimitedText(rows: string[][], delimiter: "," | "\t"): string {
  return rows
    .map((row) => row.map((cell) => serializeCell(cell, delimiter)).join(delimiter))
    .join("\n");
}

function serializeCell(value: string, delimiter: "," | "\t"): string {
  const mustQuote = value.includes(delimiter)
    || value.includes("\"")
    || value.includes("\n")
    || value.includes("\r")
    || value !== value.trim();
  return mustQuote ? `"${value.replace(/"/g, "\"\"")}"` : value;
}

function normalizeRows(rows: string[][]): string[][] {
  const columnCount = Math.max(1, ...rows.map((row) => row.length));
  return ensureShape(rows, Math.max(rows.length, 1), columnCount);
}

function ensureShape(rows: string[][], rowCount: number, columnCount: number): string[][] {
  return Array.from({ length: rowCount }, (_, rowIndex) => {
    const sourceRow = rows[rowIndex] ?? [];
    return Array.from({ length: columnCount }, (_, columnIndex) => sourceRow[columnIndex] ?? "");
  });
}

function trimRows(rows: string[][]): string[][] {
  let lastRowIndex = rows.length - 1;
  while (lastRowIndex > 0 && rows[lastRowIndex].every((cell) => cell === "")) {
    lastRowIndex -= 1;
  }

  const slicedRows = rows.slice(0, lastRowIndex + 1);
  let lastColumnIndex = Math.max(0, ...slicedRows.map((row) => row.length - 1));
  while (lastColumnIndex > 0 && slicedRows.every((row) => (row[lastColumnIndex] ?? "") === "")) {
    lastColumnIndex -= 1;
  }

  return slicedRows.map((row) => row.slice(0, lastColumnIndex + 1));
}

function looksNumeric(value: string): boolean {
  const trimmed = value.trim();
  return trimmed !== "" && Number.isFinite(Number(trimmed));
}

function toColumnLabel(index: number): string {
  let value = index + 1;
  let label = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    label = String.fromCharCode(65 + remainder) + label;
    value = Math.floor((value - 1) / 26);
  }
  return label;
}

export default CsvTableEditor;
