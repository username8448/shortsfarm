import type {TemplateDefinition} from './template';
import {
  ruleLabel,
  ruleValueLabel,
} from './labels';

export const RulesPanel = ({definition}: {definition: TemplateDefinition}) => (
  <section className="ts-card">
    <div className="ts-card-head"><h2>Правила автоматизации</h2></div>
    <dl className="rules-list">
      {Object.entries(definition.rules).map(([key, value]) => (
        <div key={key}>
          <dt>{ruleLabel(key)} <code>{key}</code></dt>
          <dd>{ruleValueLabel(value)}</dd>
        </div>
      ))}
    </dl>
  </section>
);
