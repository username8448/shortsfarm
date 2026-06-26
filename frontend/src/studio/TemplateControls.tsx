import type {Recipe} from './recipe';

export const TemplateControls = ({
  recipe,
  onChange,
}: {
  recipe: Recipe;
  onChange: (recipe: Recipe) => void;
}) => {
  const patch = <K extends keyof Recipe>(key: K, value: Recipe[K]) => {
    onChange({...recipe, [key]: value});
  };
  return (
    <>
      <label className="control">
        <span>Высота reaction · {recipe.layout.reaction_height}px</span>
        <input
          type="range"
          min="240"
          max="960"
          step="10"
          value={recipe.layout.reaction_height}
          onChange={(event) => patch('layout', {
            ...recipe.layout,
            reaction_height: Number(event.target.value),
          })}
        />
      </label>
      <label className="control">
        <span>Масштаб основного видео</span>
        <select value={recipe.layout.main_fit} onChange={(event) => patch('layout', {
          ...recipe.layout,
          main_fit: event.target.value as 'cover' | 'contain',
        })}>
          <option value="cover">cover · заполнить кадр</option>
          <option value="contain">contain · поместить целиком</option>
        </select>
      </label>
      <label className="control">
        <span>Масштаб reaction</span>
        <select value={recipe.layout.reaction_fit} onChange={(event) => patch('layout', {
          ...recipe.layout,
          reaction_fit: event.target.value as 'cover' | 'contain',
        })}>
          <option value="cover">cover · заполнить кадр</option>
          <option value="contain">contain · поместить целиком</option>
        </select>
      </label>
      <label className="control">
        <span>Фон</span>
        <input type="color" value={recipe.layout.background_color} onChange={(event) => patch('layout', {
          ...recipe.layout,
          background_color: event.target.value,
        })} />
      </label>
      <label className="control">
        <span>Громкость основного видео · {recipe.audio.main_volume.toFixed(2)}</span>
        <input type="range" min="0" max="1" step="0.05" value={recipe.audio.main_volume} onChange={(event) => patch('audio', {
          ...recipe.audio,
          main_volume: Number(event.target.value),
        })} />
      </label>
      <label className="control">
        <span>Громкость reaction · {recipe.audio.reaction_volume.toFixed(2)}</span>
        <input type="range" min="0" max="1" step="0.05" value={recipe.audio.reaction_volume} onChange={(event) => patch('audio', {
          ...recipe.audio,
          reaction_volume: Number(event.target.value),
        })} />
      </label>
      <label className="check-control">
        <input type="checkbox" checked={recipe.audio.mute_reaction} onChange={(event) => patch('audio', {
          ...recipe.audio,
          mute_reaction: event.target.checked,
        })} />
        <span>Отключить звук reaction</span>
      </label>
      <label className="control">
        <span>Верхний текст</span>
        <input maxLength={200} value={recipe.overlays.top_text} onChange={(event) => patch('overlays', {
          ...recipe.overlays,
          top_text: event.target.value,
        })} />
      </label>
      <label className="control">
        <span>Нижний текст</span>
        <input maxLength={200} value={recipe.overlays.bottom_text} onChange={(event) => patch('overlays', {
          ...recipe.overlays,
          bottom_text: event.target.value,
        })} />
      </label>
    </>
  );
};
