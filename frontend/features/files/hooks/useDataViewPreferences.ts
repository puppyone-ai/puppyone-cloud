'use client';

import type {
  EditorType,
  ViewType,
} from '@/components/ProjectsHeader';
import type { HtmlArtifactMode } from '@/components/editors/html/HtmlArtifactPreview';
import type { CsvViewMode } from '@/components/editors/spreadsheet/CsvTableViewer';
import { useState } from 'react';

export function useDataViewPreferences() {
  const [viewType, setViewTypeState] = useState<ViewType>(() => {
    if (typeof window === 'undefined') return 'explorer';
    const saved = localStorage.getItem('puppyone-view-type');
    if (saved === 'grid' || saved === 'explorer') return saved;
    return 'explorer';
  });

  const [editorType, setEditorTypeState] = useState<EditorType>(() => {
    if (typeof window === 'undefined') return 'table';
    const saved = localStorage.getItem('puppyone-editor-type');
    if (saved === 'table' || saved === 'monaco') return saved;
    return 'table';
  });

  const [htmlArtifactMode, setHtmlArtifactMode] =
    useState<HtmlArtifactMode>('preview');

  const [csvViewMode, setCsvViewModeState] = useState<CsvViewMode>(() => {
    if (typeof window === 'undefined') return 'edit';
    const saved = localStorage.getItem('puppyone-csv-view-mode');
    if (saved === 'edit' || saved === 'preview' || saved === 'source') return saved;
    return 'edit';
  });

  const setViewType = (value: ViewType) => {
    setViewTypeState(value);
    localStorage.setItem('puppyone-view-type', value);
  };

  const setEditorType = (value: EditorType) => {
    setEditorTypeState(value);
    localStorage.setItem('puppyone-editor-type', value);
  };

  const setCsvViewMode = (value: CsvViewMode) => {
    setCsvViewModeState(value);
    localStorage.setItem('puppyone-csv-view-mode', value);
  };

  return {
    viewType,
    setViewType,
    editorType,
    setEditorType,
    htmlArtifactMode,
    setHtmlArtifactMode,
    csvViewMode,
    setCsvViewMode,
  };
}
