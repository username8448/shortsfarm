import type {Recipe} from './recipe';
import {
  parameterValue,
  setRecipeParameter,
  type ParameterDefinition,
  type TemplateDefinition,
} from './template';

const groupLabels: Record<string, string> = {
  layout: 'Layout parameters',
  audio: 'Audio parameters',
  text: 'Text parameters',
  automation: 'Automation defaults',
};

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
        {(definition.values || []).map((option) => <option key={option}>{option}</option>)}
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
        <div><h2>Parameters</h2><p>Automation default и текущее test-значение разделены.</p></div>
      </div>
      {Object.entries(groups).map(([group, entries]) => (
        <div className="parameter-group" key={group}>
          <h3>{groupLabels[group] || group}</h3>
          {entries.map(([key, parameter]) => (
            <div className="parameter-row" key={key}>
              <div className="parameter-meta">
                <strong>{key}</strong>
                <small>
                  {parameter.type}
                  {parameter.min !== undefined ? ` · min ${parameter.min}` : ''}
                  {parameter.max !== undefined ? ` · max ${parameter.max}` : ''}
                  {parameter.values ? ` · ${parameter.values.join(' / ')}` : ''}
                </small>
              </div>
              <label>
                <span>Default</span>
                <ParameterInput
                  definition={parameter}
                  value={parameter.default}
                  onChange={(value) => updateDefault(key, value)}
                />
              </label>
              <label>
                <span>Test value</span>
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
