"""Madrid district-level deep dive.

Three adapters, one municipality (INE 28079, districts 01-21):

- ``padron.py`` — monthly population by district, straight off datos.madrid.es'
  own dated CSV catalogue.
- ``locales.py`` — business-premises stock, openings, closures and vacancy,
  reconstructed from a daily-refreshed current-state census that carries no
  history of its own (see that module's docstring for the snapshot/diff/chain
  design).
- ``vut.py`` — licensed short-term-rental dwellings, from the Ayuntamiento's
  change-of-use licence register.

``_shared.py`` holds the district-name/code table and small format helpers
(Spanish month names, a pure-Python DBF reader for the VUT shapefile) used by
more than one of the three.
"""
