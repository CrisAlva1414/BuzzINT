import rarfile

for year in range(2004, 2018):
    path = f"data/mineduc/raw/cargos/Directorio Docentes {year}.rar"
    try:
        with rarfile.RarFile(path) as rf:
            for e in rf.infolist():
                print(year, repr(e.filename))
                break
    except Exception as ex:
        print(year, "ERROR:", ex)