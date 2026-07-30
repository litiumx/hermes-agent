

## 🛒 SHOPPING PROTOCOL

### 1. BULK FILL (скорость)
- НИКОГДА не заполняй поля по одному.
- Для адреса: `browser_shipping_fill(full_name, street, city, state, zip, shipping_method, payment_method)`
- Для оплаты: `browser_payment_fill(cardholder_name, card_number, expiry_month, expiry_year, cvv, billing_zip)`
- Terms & Conditions отмечается АВТОМАТИЧЕСКИ в browser_payment_fill.

### 2. BILLING ZIP (точность)
- Billing ZIP НИКОГДА не равен Shipping ZIP.
- Для тестовых карт CreditClaw:
  * Casey K. Lopez → 85009 | Alex K. Garcia → 10006 | Maria S. Chen → 60607
  * Taylor J. Kim → 94117 | Logan Taylor → 98101 | Emerson Jackson → 77001

### 3. VERIFY ПЕРЕД SUBMIT
- Перед кликом на Pay/Submit/Continue → `browser_verify_all`
- Если хоть одно поле не OK → browser_payment_fill/browser_shipping_fill заново

### 4. ПОРЯДОК ДЕЙСТВИЙ
1. Найти товар (search/категория)
2. Выбрать цвет/размер/количество (кликнуть ДАЖЕ если уже выбрано)
3. Add to cart
4. Cart → Proceed to checkout
5. На checkout: **browser_shipping_fill** (ВСЁ СРАЗУ)
6. Continue to payment
7. На payment: **browser_payment_fill** (ВСЁ СРАЗУ + terms)
8. **browser_verify_all** перед Pay
9. Pay
