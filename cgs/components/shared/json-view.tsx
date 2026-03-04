interface JsonViewProps {
  value: unknown;
}

export function JsonView({ value }: JsonViewProps): JSX.Element {
  return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>;
}
