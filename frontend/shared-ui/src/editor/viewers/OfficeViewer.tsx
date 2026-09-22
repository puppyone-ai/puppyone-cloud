"use client";

import { useEffect, useState } from "react";
import type { EditorViewerContext } from "../viewerTypes";

type OfficeState =
  | { status: "idle" | "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; result: OfficePreviewResult };

type OfficePreviewResult =
  | { kind: "word"; html: string; warnings: string[] }
  | { kind: "spreadsheet"; sheets: SpreadsheetSheet[] }
  | { kind: "presentation"; slides: PresentationSlide[] }
  | { kind: "opendocument"; title: string; lines: string[] }
  | { kind: "unsupported"; message: string };

type SpreadsheetSheet = {
  name: string;
  rows: string[][];
  totalRows: number;
  totalColumns: number;
};

type PresentationSlide = {
  index: number;
  title: string;
  lines: string[];
};

type MammothApi = {
  convertToHtml: (
    input: { arrayBuffer: ArrayBuffer },
    options?: {
      convertImage?: unknown;
      includeDefaultStyleMap?: boolean;
    },
  ) => Promise<{
    value: string;
    messages: Array<{ message: string }>;
  }>;
  images: {
    dataUri: unknown;
  };
};

const MAX_SHEET_ROWS = 250;
const MAX_SHEET_COLUMNS = 36;
const MAX_ODF_LINES = 400;
const LEGACY_WORD_EXTENSIONS = new Set(["doc"]);
const LEGACY_PRESENTATION_EXTENSIONS = new Set(["ppt", "pps"]);

export function OfficeViewer({
  document,
  fileUrl,
  fileUrlLoading,
  fileUrlError,
}: EditorViewerContext) {
  const [state, setState] = useState<OfficeState>({ status: "idle" });
  const [activeSheet, setActiveSheet] = useState(0);

  useEffect(() => {
    setActiveSheet(0);
  }, [document.path]);

  useEffect(() => {
    if (!fileUrl) {
      setState(fileUrlLoading ? { status: "loading" } : { status: "idle" });
      return undefined;
    }

    let cancelled = false;

    setState({ status: "loading" });
    loadOfficePreview(fileUrl, document.name)
      .then((result) => {
        if (!cancelled) setState({ status: "ready", result });
      })
      .catch((error) => {
        if (!cancelled) {
          setState({
            status: "error",
            message: error instanceof Error ? error.message : String(error),
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [document.name, fileUrl, fileUrlLoading]);

  if (fileUrlError) {
    return <div className="editor-state danger">Failed to load file: {fileUrlError}</div>;
  }

  if ((fileUrlLoading || state.status === "loading") && !fileUrl) {
    return <div className="editor-state">Loading preview...</div>;
  }

  if (!fileUrl && state.status !== "ready") {
    return <div className="editor-state">No preview available for this file.</div>;
  }

  return (
    <div className="office-preview">
      <div className="office-preview__body">
        {state.status === "error" && (
          <OfficeEmptyState title="Preview failed" message={state.message} />
        )}
        {state.status === "idle" || state.status === "loading" ? (
          <div className="editor-state">Loading preview...</div>
        ) : null}
        {state.status === "ready" && (
          <OfficePreviewContent
            result={state.result}
            activeSheet={activeSheet}
            onActiveSheetChange={setActiveSheet}
          />
        )}
      </div>
    </div>
  );
}

function OfficePreviewContent({
  result,
  activeSheet,
  onActiveSheetChange,
}: {
  result: OfficePreviewResult;
  activeSheet: number;
  onActiveSheetChange: (index: number) => void;
}) {
  if (result.kind === "unsupported") {
    return <OfficeEmptyState title="Preview not available" message={result.message} />;
  }

  if (result.kind === "word") {
    return (
      <div className="office-document-preview">
        <article
          className="office-document-page"
          dangerouslySetInnerHTML={{ __html: result.html || "<p></p>" }}
        />
      </div>
    );
  }

  if (result.kind === "spreadsheet") {
    if (result.sheets.length === 0) {
      return <OfficeEmptyState title="Empty workbook" message="No sheets were found in this workbook." />;
    }

    const selectedSheet = result.sheets[Math.min(activeSheet, result.sheets.length - 1)];
    const hasTruncatedRows = selectedSheet.totalRows > selectedSheet.rows.length;
    const hasTruncatedColumns = selectedSheet.totalColumns > MAX_SHEET_COLUMNS;

    return (
      <div className="office-spreadsheet-preview">
        <div className="office-spreadsheet-tabs" role="tablist" aria-label="Sheets">
          {result.sheets.map((sheet, index) => (
            <button
              key={sheet.name}
              type="button"
              role="tab"
              aria-selected={index === activeSheet}
              onClick={() => onActiveSheetChange(index)}
            >
              {sheet.name}
            </button>
          ))}
        </div>
        <div className="office-spreadsheet-grid-wrap">
          <table className="office-spreadsheet-grid">
            <tbody>
              {selectedSheet.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  <th scope="row">{rowIndex + 1}</th>
                  {row.map((cell, columnIndex) => (
                    <td key={`${rowIndex}-${columnIndex}`}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {(hasTruncatedRows || hasTruncatedColumns) && (
          <div className="office-preview__note">
            Showing {selectedSheet.rows.length} of {selectedSheet.totalRows} rows
            {hasTruncatedColumns ? ` and ${MAX_SHEET_COLUMNS} of ${selectedSheet.totalColumns} columns` : ""}.
          </div>
        )}
      </div>
    );
  }

  if (result.kind === "presentation") {
    if (result.slides.length === 0) {
      return <OfficeEmptyState title="Empty presentation" message="No slide text was found in this presentation." />;
    }

    return (
      <div className="office-presentation-preview">
        {result.slides.map((slide) => (
          <article className="office-slide-card" key={slide.index}>
            <div className="office-slide-card__number">{slide.index}</div>
            <div className="office-slide-card__content">
              <h2>{slide.title}</h2>
              {slide.lines.length > 0 ? (
                <ul>
                  {slide.lines.map((line, index) => (
                    <li key={`${line}-${index}`}>{line}</li>
                  ))}
                </ul>
              ) : (
                <p>No readable text on this slide.</p>
              )}
            </div>
          </article>
        ))}
      </div>
    );
  }

  return (
    <div className="office-document-preview">
      <article className="office-document-page">
        <h1>{result.title}</h1>
        {result.lines.length > 0 ? (
          result.lines.map((line, index) => <p key={`${line}-${index}`}>{line}</p>)
        ) : (
          <p>No readable text was found.</p>
        )}
      </article>
    </div>
  );
}

function OfficeEmptyState({ title, message }: { title: string; message: string }) {
  return (
    <div className="office-preview-empty">
      <strong>{title}</strong>
      <span>{message}</span>
    </div>
  );
}

async function loadOfficePreview(fileUrl: string, filename: string): Promise<OfficePreviewResult> {
  const extension = getExtension(filename);

  if (LEGACY_WORD_EXTENSIONS.has(extension)) {
    return {
      kind: "unsupported",
      message: "Legacy .doc files need the native Office format bridge. Use .docx for lightweight preview.",
    };
  }

  if (LEGACY_PRESENTATION_EXTENSIONS.has(extension)) {
    return {
      kind: "unsupported",
      message: "Legacy PowerPoint files need the native Office format bridge. Use .pptx for lightweight preview.",
    };
  }

  const arrayBuffer = await fetchArrayBuffer(fileUrl);

  if (isWordExtension(extension)) return parseWordDocument(arrayBuffer);
  if (isSpreadsheetExtension(extension)) {
    try {
      return await parseSpreadsheet(arrayBuffer);
    } catch (error) {
      if (!isOpenDocumentExtension(extension)) throw error;
    }
  }
  if (isPresentationExtension(extension)) return parsePresentation(arrayBuffer);
  if (isOpenDocumentExtension(extension)) return parseOpenDocument(arrayBuffer, filename);

  return {
    kind: "unsupported",
    message: "This Office format is not available in the lightweight preview.",
  };
}

async function fetchArrayBuffer(fileUrl: string): Promise<ArrayBuffer> {
  const response = await fetch(fileUrl);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.arrayBuffer();
}

async function parseWordDocument(arrayBuffer: ArrayBuffer): Promise<OfficePreviewResult> {
  const mammothModule = await import("mammoth");
  const mammoth = ("default" in mammothModule ? mammothModule.default : mammothModule) as MammothApi;
  const result = await mammoth.convertToHtml(
    { arrayBuffer },
    {
      convertImage: mammoth.images.dataUri,
      includeDefaultStyleMap: true,
    },
  );

  return {
    kind: "word",
    html: result.value,
    warnings: result.messages.map((message) => message.message),
  };
}

async function parseSpreadsheet(arrayBuffer: ArrayBuffer): Promise<OfficePreviewResult> {
  const XLSX = await import("xlsx");
  const workbook = XLSX.read(arrayBuffer, {
    type: "array",
    cellDates: true,
    dense: false,
  });

  return {
    kind: "spreadsheet",
    sheets: workbook.SheetNames.slice(0, 12).map((sheetName) => {
      const worksheet = workbook.Sheets[sheetName];
      const range = worksheet?.["!ref"] ? XLSX.utils.decode_range(worksheet["!ref"]) : null;
      const rows = XLSX.utils.sheet_to_json<Array<string | number | boolean | Date | null>>(worksheet, {
        header: 1,
        blankrows: false,
        defval: "",
        raw: false,
      });
      const visibleRows = rows.slice(0, MAX_SHEET_ROWS).map((row) => (
        row.slice(0, MAX_SHEET_COLUMNS).map(formatCellValue)
      ));
      const totalRows = range ? range.e.r - range.s.r + 1 : rows.length;
      const totalColumns = range ? range.e.c - range.s.c + 1 : Math.max(0, ...rows.map((row) => row.length));

      return {
        name: sheetName,
        rows: normalizeSpreadsheetRows(visibleRows),
        totalRows,
        totalColumns,
      };
    }),
  };
}

async function parsePresentation(arrayBuffer: ArrayBuffer): Promise<OfficePreviewResult> {
  const { default: JSZip } = await import("jszip");
  const zip = await JSZip.loadAsync(arrayBuffer);
  const slideEntries = Object.values(zip.files)
    .filter((file) => /^ppt\/slides\/slide\d+\.xml$/i.test(file.name))
    .sort((left, right) => getSlideIndex(left.name) - getSlideIndex(right.name));

  const slides = await Promise.all(
    slideEntries.map(async (file, index) => {
      const xml = await file.async("text");
      const lines = extractXmlText(xml);
      const [title, ...body] = lines;

      return {
        index: index + 1,
        title: title || `Slide ${index + 1}`,
        lines: body,
      };
    }),
  );

  return { kind: "presentation", slides };
}

async function parseOpenDocument(arrayBuffer: ArrayBuffer, filename: string): Promise<OfficePreviewResult> {
  const { default: JSZip } = await import("jszip");
  const zip = await JSZip.loadAsync(arrayBuffer);
  const contentFile = zip.file("content.xml");

  if (!contentFile) {
    return {
      kind: "unsupported",
      message: "This OpenDocument file does not contain readable content.xml.",
    };
  }

  const xml = await contentFile.async("text");
  const lines = extractXmlText(xml).slice(0, MAX_ODF_LINES);

  return {
    kind: "opendocument",
    title: filename,
    lines,
  };
}

function normalizeSpreadsheetRows(rows: string[][]): string[][] {
  const columnCount = Math.max(1, ...rows.map((row) => row.length));
  return rows.map((row) => {
    const nextRow = row.slice();
    while (nextRow.length < columnCount) nextRow.push("");
    return nextRow;
  });
}

function formatCellValue(value: string | number | boolean | Date | null | undefined): string {
  if (value === null || value === undefined) return "";
  if (value instanceof Date) return value.toLocaleDateString();
  return String(value);
}

function extractXmlText(xml: string): string[] {
  const doc = new DOMParser().parseFromString(xml, "application/xml");
  const parserError = doc.querySelector("parsererror");
  if (parserError) return [];

  const nodes = Array.from(doc.getElementsByTagName("*"));
  const paragraphNodes = nodes.filter((node) => node.localName === "p");
  const textNodes = paragraphNodes.length > 0
    ? paragraphNodes
    : nodes.filter((node) => node.localName === "t");
  const textLines = textNodes
    .map((node) => node.textContent?.replace(/\s+/g, " ").trim() ?? "")
    .filter(Boolean);

  return dedupeConsecutive(textLines);
}

function dedupeConsecutive(lines: string[]): string[] {
  const result: string[] = [];
  for (const line of lines) {
    if (result[result.length - 1] !== line) result.push(line);
  }
  return result;
}

function getSlideIndex(path: string): number {
  const match = /slide(\d+)\.xml$/i.exec(path);
  return match ? Number(match[1]) : 0;
}

function getExtension(filename: string): string {
  const match = /\.([^.]+)$/.exec(filename.toLowerCase());
  return match ? match[1] : "";
}

function isWordExtension(extension: string): boolean {
  return extension === "docx";
}

function isSpreadsheetExtension(extension: string): boolean {
  return extension === "xlsx" || extension === "xls" || extension === "xlsm" || extension === "xlsb" || extension === "ods";
}

function isPresentationExtension(extension: string): boolean {
  return extension === "pptx" || extension === "ppsx";
}

function isOpenDocumentExtension(extension: string): boolean {
  return extension === "odt" || extension === "ods" || extension === "odp" || extension === "ott" || extension === "ots" || extension === "otp";
}
