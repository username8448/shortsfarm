import type {ReactNode} from 'react';

export const StudioLayout = ({
  media,
  preview,
  controls,
}: {
  media: ReactNode;
  preview: ReactNode;
  controls: ReactNode;
}) => (
  <div className="studio-layout">
    <aside className="studio-panel studio-media">{media}</aside>
    <main className="studio-preview">{preview}</main>
    <aside className="studio-panel studio-controls">{controls}</aside>
  </div>
);
