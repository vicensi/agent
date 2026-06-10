from gerar_dataset_ecommerce import gerar_dataset_ecommerce, diagnostico

df = gerar_dataset_ecommerce(n_pedidos=190_000, seed=42)
diagnostico(df)          # imprime resumo das armadilhas
df.to_csv("ecommerce.csv", index=False)
