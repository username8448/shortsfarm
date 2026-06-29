import {StudioPage} from './studio/StudioPage';
import {VideoPlayerPage} from './workbench/VideoPlayerPage';

export const App = ({embedded = false}: {embedded?: boolean}) => {
  const params = new URLSearchParams(window.location.search);
  const isPlayerRoute = !embedded && window.location.pathname.startsWith('/player');

  if (isPlayerRoute) {
    return <VideoPlayerPage initialPath={params.get('path') || ''} />;
  }

  return <StudioPage embedded={embedded} />;
};
