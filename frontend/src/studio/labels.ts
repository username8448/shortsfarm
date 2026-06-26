import type {
  ParameterDefinition,
  TemplateStatus,
} from './template';

export const statusLabel = (status: TemplateStatus | string): string => ({
  draft: 'черновик',
  active: 'активен',
  archived: 'в архиве',
  queued: 'в очереди',
  running: 'в работе',
  rendering: 'рендерится',
  done: 'готово',
  failed: 'ошибка',
  cancelled: 'отменено',
}[status] || String(status || '—'));

export const slotLabel = (key: string): string => ({
  main: 'Основное видео',
  reaction: 'Видео реакции',
}[key] || key);

export const slotPropertyLabel = (key: string): string => ({
  duration_policy: 'Длительность',
  source: 'Источник',
  playback: 'Воспроизведение',
}[key] || key);

export const slotValueLabel = (value: unknown): string => ({
  defines_output_duration: 'задаёт длительность результата',
  reaction_asset_or_pool: 'reaction-файл или пул реакций',
  loop: 'повторять по кругу',
  video: 'видео',
}[String(value)] || String(value ?? '—'));

export const parameterLabel = (key: string): string => ({
  reaction_position: 'Положение реакции',
  reaction_height: 'Высота блока реакции',
  pip_position: 'Угол picture-in-picture',
  main_fit: 'Масштаб основного видео',
  reaction_fit: 'Масштаб реакции',
  background_color: 'Цвет фона',
  main_volume: 'Громкость основного видео',
  reaction_volume: 'Громкость реакции',
  mute_reaction: 'Отключить звук реакции',
  top_text: 'Верхний текст',
  bottom_text: 'Нижний текст',
}[key] || key);

export const parameterTypeLabel = (type: ParameterDefinition['type']): string => ({
  number: 'число',
  select: 'выбор',
  boolean: 'да/нет',
  text: 'текст',
  color: 'цвет',
}[type] || type);

export const groupLabel = (group: string): string => ({
  layout: 'Параметры расположения',
  audio: 'Параметры звука',
  text: 'Текстовые параметры',
  automation: 'Стандартные значения автоматизации',
}[group] || group);

export const fitLabel = (value: string): string => ({
  cover: 'cover · заполнить кадр',
  contain: 'contain · поместить целиком',
  top: 'сверху',
  bottom: 'снизу',
  pip: 'picture-in-picture',
  none: 'без реакции',
  top_left: 'верхний левый',
  top_right: 'верхний правый',
  bottom_left: 'нижний левый',
  bottom_right: 'нижний правый',
}[value] || value);

export const ruleLabel = (key: string): string => ({
  output_duration: 'Длительность результата',
  reaction_playback: 'Воспроизведение реакции',
  output_aspect: 'Формат кадра',
  output_folder: 'Папка результата',
  renderer: 'Renderer',
  renderer_adapter: 'Renderer adapter',
  composition_id: 'Remotion composition',
  layout_variant: 'Вариант layout',
}[key] || key.replaceAll('_', ' '));

export const ruleValueLabel = (value: unknown): string => ({
  'main.duration': 'по длительности основного видео',
  loop: 'повторять по кругу',
  edits: 'workspace_root/edits',
  remotion: 'Remotion',
  reaction_layout: 'Reaction layout adapter',
  ReactionLayoutTemplate: 'ReactionLayoutTemplate',
  top_reaction: 'реакция сверху',
  bottom_reaction: 'реакция снизу',
  picture_in_picture: 'picture-in-picture',
}[String(value)] || String(value ?? '—'));

export const folderSectionLabel = (section: string): string => ({
  sources: 'Исходники',
  cuts: 'Нарезки',
  prepared: 'Подготовленные',
  edits: 'Результаты монтажа',
  ready: 'Готовые',
  published: 'Опубликованные',
}[section] || section);

export const workspacePathLabel = (path: string): string => {
  const parts = String(path || '').split('/').filter(Boolean);
  if (!parts.length) return path || '';
  const [first, ...rest] = parts;
  return [folderSectionLabel(first), ...rest].join('/');
};
