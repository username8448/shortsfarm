import type {DetailedHTMLProps, HTMLAttributes} from 'react';

type MediaChromeElementProps = DetailedHTMLProps<
  HTMLAttributes<HTMLElement>,
  HTMLElement
> & {
  slot?: string;
};

declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      'media-controller': MediaChromeElementProps;
      'media-control-bar': MediaChromeElementProps;
      'media-play-button': MediaChromeElementProps;
      'media-seek-backward-button': MediaChromeElementProps;
      'media-seek-forward-button': MediaChromeElementProps;
      'media-time-display': MediaChromeElementProps;
      'media-time-range': MediaChromeElementProps;
      'media-playback-rate-button': MediaChromeElementProps;
      'media-mute-button': MediaChromeElementProps;
      'media-volume-range': MediaChromeElementProps;
      'media-pip-button': MediaChromeElementProps;
      'media-fullscreen-button': MediaChromeElementProps;
    }
  }
}

export {};
