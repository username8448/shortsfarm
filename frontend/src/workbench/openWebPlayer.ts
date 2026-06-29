export const webPlayerUrl = (workspacePath: string): string =>
  `/player?path=${encodeURIComponent(workspacePath)}`;

export const openWebPlayer = (workspacePath: string): void => {
  const path = workspacePath.trim();
  if (!path) return;
  window.open(webPlayerUrl(path), '_blank', 'noopener,noreferrer');
};
