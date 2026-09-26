---
title: Market monitor
toc: false
---

```js
import { countryDashboard } from "./components/economy.js";
import { loadCountry } from "./components/country-data.js";
const economy = await FileAttachment("data/economy-overview.json").json();
const cities = await FileAttachment("data/city-coverage.json").json();
const loadScreener = () => FileAttachment("data/screener.json").json();
display(countryDashboard(economy, cities, loadCountry, loadScreener));
```
