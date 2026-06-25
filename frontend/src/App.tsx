import {StudioPage} from './studio/StudioPage';

export const App = ({embedded = false}: {embedded?: boolean}) => (
  <StudioPage embedded={embedded} />
);
