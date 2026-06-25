import type {TemplateDefinition} from './template';

export const RulesPanel = ({definition}: {definition: TemplateDefinition}) => (
  <section className="ts-card">
    <div className="ts-card-head"><h2>Automation Rules</h2></div>
    <dl className="rules-list">
      {Object.entries(definition.rules).map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll('_', ' ')}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  </section>
);
