def calcular_promedio(numeros):
    suma = 0
    for numero in numeros:
        suma += numero
    if len(numeros) > 0:
        promedio = suma / len(numeros)
        print("El promedio es:", promedio)
    else:
        print("No se puede calcular el promedio de una lista vacía.")