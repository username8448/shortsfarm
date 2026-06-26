import type {Recipe} from './recipe';
import {
  fitLabel,
  groupLabel,
  parameterLabel,
  parameterTypeLabel,
} from './labels';
import {
  parameterValue,
  setRecipeParameter,
  type ParameterDefinition,
  type TemplateDefinition,
} from './template';

const ParameterInput = ({
  definition,
  value,
  onChange,
}: {
  definition: ParameterDefinition;
  value: string | number | boolean;
  onChange: (value: string | number | boolean) => void;
}) => {
  if (definition.type === 'boolean') {
    return <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />;
  }
  if (definition.type === 'select') {
    return (
      <select value={String(value)} onChange={(event) => onChange(event.target.value)}>
        {(definition.values || []).map((option) => (
          <option key={option} value={option}>{fitLabel(option)}</option>
        ))}
      </select>
    );
  }
  if (definition.type === 'color') {
    return <input type="color" value={String(value)} onChange={(event) => onChange(event.target.value)} />;
  }
  return (
    <input
      type={definition.type === 'number' ? 'number' : 'text'}
      min={definition.min}
      max={definition.max}
      maxLength={definition.max_length}
      step={definition.type === 'number' && Number(definition.max) <= 1 ? 0.05 : 1}
      value={String(value)}
      onChange={(event) => onChange(
        definition.type === 'number' ? Number(event.target.value) : event.target.value,
      )}
    />
  );
};

export const ParametersPanel = ({
  definition,
  recipe,
  onDefinitionChange,
  onRecipeChange,
}: {
  definition: TemplateDefinition;
  recipe: Recipe;
  onDefinitionChange: (definition: TemplateDefinition) => void;
  onRecipeChange: (recipe: Recipe) => void;
}) => {
  const groups = Object.entries(definition.parameters).reduce<Record<string, Array<[string, ParameterDefinition]>>>(
    (result, entry) => {
      const group = entry[1].group || 'automation';
      (result[group] ||= []).push(entry);
      return result;
    },
    {},
  );
  const updateDefault = (key: string, value: string | number | boolean) => {
    onDefinitionChange({
      ...definition,
      parameters: {
        ...definition.parameters,
        [key]: {...definition.parameters[key], default: value},
      },
    });
  };
  return (
    <section className="ts-card parameters-card">
      <div className="ts-card-head">
        <div><h2>Параметры</h2><p>Стандартное значение шаблона и текущее тестовое значение разделены.</p></div>
      </div>
      {Object.entries(groups).map(([group, entries]) => (
        <div className="parameter-group" key={group}>
          <h3>{groupLabel(group)}</h3>
          {entries.map(([key, parameter]) => (
            <div className="parameter-row" key={key}>
              <div className="parameter-meta">
                <strong>{parameterLabel(key)}</strong>
                <code>{key}</code>
                <small>
                  {parameterTypeLabel(parameter.type)}
                  {parameter.min !== undefined ? ` · минимум ${parameter.min}` : ''}
                  {parameter.max !== undefined ? ` · максимум ${parameter.max}` : ''}
                  {parameter.values ? ` · ${parameter.values.map(fitLabel).join(' / ')}` : ''}
                </small>
              </div>
              <label>
                <span>По умолчанию</span>
                <ParameterInput
                  definition={parameter}
                  value={parameter.default}
                  onChange={(value) => updateDefault(key, value)}
                />
              </label>
              <label>
                <span>Тестовое значение</span>
                <ParameterInput
                  definition={parameter}
                  value={parameterValue(recipe, key)}
                  onChange={(value) => onRecipeChange(setRecipeParameter(recipe, key, value))}
                />
              </label>
            </div>
          ))}
        </div>
      ))}
    </section>
  );
};
