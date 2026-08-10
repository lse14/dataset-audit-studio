import { ChevronDown, Cpu, Network, Settings2 } from 'lucide-react'
import { useMemo, useState } from 'react'

import { componentConfigView } from '../componentConfigViews'
import { componentHelpText } from '../taskConfigHelp'
import type { ComponentConfigValue, ComponentManifest, JsonSchema } from '../types'
import { FieldHelp } from './FieldHelp'
import { SchemaObjectFields } from './SchemaField'

export function buildDefaultComponentConfig(
  manifests: ComponentManifest[],
): Record<string, ComponentConfigValue> {
  return Object.fromEntries(manifests.map((manifest) => [
    manifest.id,
    {
      enabled: manifest.activation === 'required'
        || (manifest.activation === 'optional' && manifest.recommended_enabled),
      config: cloneObject(manifest.default_config),
    },
  ]))
}

export function ComponentConfigEditor({
  manifests,
  value,
  onChange,
  profileOwnedComponentIds = [],
  hiddenConfigFields = {},
}: {
  manifests: ComponentManifest[]
  value: Record<string, ComponentConfigValue>
  onChange: (value: Record<string, ComponentConfigValue>) => void
  profileOwnedComponentIds?: string[]
  hiddenConfigFields?: Record<string, readonly string[]>
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [showAdvanced, setShowAdvanced] = useState(false)
  const profileLocked = profileOwnedComponentIds.length > 0
  const dependencies = useMemo(() => dependencyMap(manifests), [manifests])
  const groups = useMemo(() => {
    const result = new Map<string, ComponentManifest[]>()
    for (const manifest of manifests) {
      const items = result.get(manifest.ui_group) ?? []
      items.push(manifest)
      result.set(manifest.ui_group, items)
    }
    return result
  }, [manifests])

  const update = (componentId: string, next: ComponentConfigValue) => {
    onChange(synchronizeClusteringScope(value, componentId, next))
  }

  return (
    <div className="component-config-editor">
      {[...groups.entries()].map(([group, items]) => (
        <section className="component-config-group" key={group}>
          <header>
            <h3>{groupLabel(group)}</h3>
            <span>{items.length} 个组件</span>
            {group === 'analysis' ? (
              <button
                className="icon-button"
                onClick={() => setShowAdvanced((value) => !value)}
                title="高级分析设置"
                type="button"
              >
                <Settings2 size={15} />
              </button>
            ) : null}
          </header>
          <div className="component-config-list">
            {items.map((manifest) => {
              if (!showAdvanced && isAdvancedAnalysis(manifest.id)) return null
              const current = value[manifest.id]
              if (!current) return null
              const open = expanded.has(manifest.id)
              const view = componentConfigView(manifest, manifests)
              const configSource = value[view.configSourceId]
              if (!configSource) return null
              const profileOwned = profileOwnedComponentIds.includes(manifest.id)
              const editorSchema = hideObjectSchemaFields(
                componentEditorSchema(manifest.id, view.schema, profileLocked),
                hiddenConfigFields[view.configSourceId] ?? [],
              )
              const properties = Object.keys(editorSchema.properties ?? {}).length
              const dependencyNames = (dependencies.get(manifest.id) ?? [])
                .map((id) => manifests.find((item) => item.id === id)?.display_name ?? id)
              return (
                <article className={`component-config-row ${current.enabled ? 'enabled' : ''}`} key={manifest.id}>
                  <div className="component-config-summary">
                    <div className="component-title-cell">
                      <button
                        aria-expanded={open}
                        className="component-expand"
                        disabled={properties === 0}
                        onClick={() => setExpanded((previous) => toggleSet(previous, manifest.id))}
                        type="button"
                      >
                        <ChevronDown className={open ? 'expanded' : ''} size={17} />
                        <span>
                          <strong>{manifest.display_name}</strong>
                          <code>{manifest.id}</code>
                        </span>
                      </button>
                      <FieldHelp
                        label={manifest.display_name}
                        text={componentHelpText(manifest.id, manifest.display_name)}
                      />
                    </div>
                    <span className="component-runtime">
                      {manifest.execution === 'gpu_process' ? <Settings2 size={14} /> : <Cpu size={14} />}
                      {executionLabel(manifest.execution)}
                    </span>
                    <span className="component-dependencies" title={dependencyNames.join('、')}>
                      <Network size={14} />
                      {dependencyNames.length ? `${dependencyNames.length} 个依赖` : '无组件依赖'}
                    </span>
                    <ActivationControl
                      manifest={manifest}
                      profileLocked={profileOwned}
                      onChange={(enabled) => update(manifest.id, {
                        enabled,
                        config: 'enabled' in current.config
                          ? { ...current.config, enabled }
                          : current.config,
                      })}
                      value={current.enabled}
                    />
                  </div>
                  {open && properties > 0 ? (
                    <div className="component-config-fields">
                      <SchemaObjectFields
                        onChange={(config) => update(view.configSourceId, { ...configSource, config })}
                        root={editorSchema}
                        schema={editorSchema}
                        value={configSource.config}
                      />
                    </div>
                  ) : null}
                </article>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}

function hideObjectSchemaFields(schema: JsonSchema, fieldNames: readonly string[]): JsonSchema {
  if (fieldNames.length === 0) return schema
  const hidden = new Set(fieldNames)
  return {
    ...schema,
    properties: Object.fromEntries(
      Object.entries(schema.properties ?? {}).filter(([fieldName]) => !hidden.has(fieldName)),
    ),
    ...(schema.required
      ? { required: schema.required.filter((fieldName) => !hidden.has(fieldName)) }
      : {}),
  }
}

function componentEditorSchema(
  componentId: string,
  schema: JsonSchema,
  profileLocked: boolean,
): JsonSchema {
  if (profileLocked && componentId === 'cluster.hierarchy') {
    return scopeModeReadOnly(schema)
  }
  return schema
}

function scopeModeReadOnly(schema: JsonSchema): JsonSchema {
  const properties = schema.properties
  const scopeMode = properties?.scope_mode
  if (!properties || !scopeMode) return schema
  return {
    ...schema,
    properties: {
      ...properties,
      scope_mode: { ...scopeMode, readOnly: true },
    },
  }
}

function synchronizeClusteringScope(
  current: Record<string, ComponentConfigValue>,
  componentId: string,
  next: ComponentConfigValue,
): Record<string, ComponentConfigValue> {
  const updated = { ...current, [componentId]: next }
  return updated
}

function isAdvancedAnalysis(componentId: string): boolean {
  return componentId === 'embedding.semantic'
    || componentId === 'cluster.hierarchy'
    || componentId === 'analysis.sae'
}

function ActivationControl({
  manifest,
  value,
  onChange,
  profileLocked,
}: {
  manifest: ComponentManifest
  value: boolean
  onChange: (value: boolean) => void
  profileLocked: boolean
}) {
  if (manifest.activation === 'auto') {
    return <span className="activation-label auto">按依赖</span>
  }
  const required = manifest.activation === 'required'
  const locked = profileLocked || required
  return (
    <label className={`toggle-control component-toggle ${locked ? 'disabled' : ''}`}>
      <input
        checked={required || value}
        disabled={locked}
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
      <span className="toggle-track"><i /></span>
      <strong>{required ? '必需' : value ? '启用' : '关闭'}</strong>
    </label>
  )
}

function dependencyMap(manifests: ComponentManifest[]): Map<string, string[]> {
  const producers = new Map<string, string>()
  for (const manifest of manifests) {
    for (const capability of manifest.produces) producers.set(capability, manifest.id)
  }
  return new Map(manifests.map((manifest) => [
    manifest.id,
    [...new Set(manifest.consumes
      .map((item) => producers.get(item.capability))
      .filter((item): item is string => Boolean(item)))],
  ]))
}

function toggleSet(previous: Set<string>, value: string): Set<string> {
  const next = new Set(previous)
  if (next.has(value)) next.delete(value)
  else next.add(value)
  return next
}

function cloneObject<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function groupLabel(value: string): string {
  const labels: Record<string, string> = {
    input: '输入与技术指标',
    screening: '评分与风险证据',
    analysis: '画风与聚类分析',
    output: '缓存与输出',
  }
  return labels[value] ?? value
}

function executionLabel(value: ComponentManifest['execution']): string {
  if (value === 'gpu_process') return 'GPU 子进程'
  if (value === 'cpu_process') return 'CPU 子进程'
  return 'CPU 内联'
}
