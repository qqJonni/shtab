// Переиспользуемый калькулятор для форм документов
// Автоподсчёт: строка = qty × price; итого; НДС; всего

function DocCalc(tableId, opts) {
  this.table = document.getElementById(tableId);
  this.qtyCol = opts.qtyCol || 'quantity';
  this.priceCol = opts.priceCol || 'price';
  this.amountCol = opts.amountCol || 'amount';
  this.totalEl = document.getElementById(opts.totalId || 'calcTotal');
  this.vatEl = document.getElementById(opts.vatId || 'calcVat');
  this.grandEl = document.getElementById(opts.grandId || 'calcGrand');
  this.vatModeEl = document.getElementById(opts.vatModeId || 'vatMode');
  this.vatRateEl = document.getElementById(opts.vatRateId || 'vatRate');
  this.rowCounter = 0;
  this.onCalc = opts.onCalc || null;
  this.init();
}

DocCalc.prototype.init = function() {
  var self = this;
  this.table.addEventListener('input', function() { self.calc(); });
  if (this.vatModeEl) this.vatModeEl.addEventListener('change', function() { self.calc(); });
  if (this.vatRateEl) this.vatRateEl.addEventListener('input', function() { self.calc(); });
  this.calc();
};

DocCalc.prototype.calc = function() {
  var rows = this.table.querySelectorAll('tbody tr');
  var total = 0;
  rows.forEach(function(row) {
    var qty = parseFloat(row.querySelector('[data-col="' + this.qtyCol + '"]')?.value) || 0;
    var price = parseFloat(row.querySelector('[data-col="' + this.priceCol + '"]')?.value) || 0;
    var amount = qty * price;
    var amountEl = row.querySelector('[data-col="' + this.amountCol + '"]');
    if (amountEl) amountEl.textContent = amount.toFixed(2);
    total += amount;
  }.bind(this));

  if (this.totalEl) this.totalEl.textContent = total.toFixed(2);

  var vatMode = this.vatModeEl ? this.vatModeEl.value : 'none';
  var vatRate = parseFloat(this.vatRateEl ? this.vatRateEl.value : 20) || 0;
  var vatAmount = 0;
  var grandTotal = total;

  if (vatMode === 'on_top') {
    vatAmount = total * vatRate / 100;
    grandTotal = total + vatAmount;
  } else if (vatMode === 'included') {
    vatAmount = total * vatRate / (100 + vatRate);
    grandTotal = total;
  }

  if (this.vatEl) this.vatEl.textContent = vatAmount.toFixed(2);
  if (this.grandEl) this.grandEl.textContent = grandTotal.toFixed(2);

  if (this.onCalc) this.onCalc(total, vatAmount, grandTotal);
};

DocCalc.prototype.addRow = function(data) {
  data = data || {};
  this.rowCounter++;
  var tbody = this.table.querySelector('tbody');
  var tr = document.createElement('tr');
  tr.innerHTML = this.rowTemplate(this.rowCounter, data);
  tbody.appendChild(tr);
  this.calc();
  return tr;
};

DocCalc.prototype.removeRow = function(btn) {
  var row = btn.closest('tr');
  if (row) row.remove();
  this.renumber();
  this.calc();
};

DocCalc.prototype.renumber = function() {
  var rows = this.table.querySelectorAll('tbody tr');
  rows.forEach(function(row, i) {
    var numEl = row.querySelector('.row-num');
    if (numEl) numEl.textContent = i + 1;
  });
  this.rowCounter = rows.length;
};

// Override per form type
DocCalc.prototype.rowTemplate = function(num, data) {
  return '<td>' + num + '</td><td>—</td>';
};

DocCalc.prototype.collectData = function() {
  var items = [];
  var rows = this.table.querySelectorAll('tbody tr');
  rows.forEach(function(row) {
    var item = {};
    row.querySelectorAll('[data-col]').forEach(function(el) {
      item[el.dataset.col] = el.value !== undefined ? el.value : el.textContent;
    });
    items.push(item);
  });
  return items;
};
