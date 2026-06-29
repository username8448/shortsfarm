export const webPlayerUrl = (workspacePath: string): string =>
  `/player?path=${encodeURIComponent(workspacePath)}`;

declare global {
  interface Window {
    shortsFarmOpenVideoLightbox?: (
      workspacePath: string,
      options?: {title?: string; startMode?: 'viewer' | 'workbench'},
    ) => boolean;
  }
}

export const openWebPlayer = (workspacePath: string): void => {
  const path = workspacePath.trim();
  if (!path) return;
  if (
    typeof window.shortsFarmOpenVideoLightbox === 'function'
    && window.shortsFarmOpenVideoLightbox(path) !== false
  ) {
    return;
  }
  window.open(webPlayerUrl(path), '_blank', 'noopener,noreferrer');
};
