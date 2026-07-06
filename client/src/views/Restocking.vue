<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <!-- Budget allocation card -->
      <div class="card budget-card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budgetLabel') }}</h3>
        </div>
        <div class="budget-content">
          <div class="budget-slider-row">
            <span class="slider-bound">{{ formatCurrency(0, currentCurrency) }}</span>
            <input
              type="range"
              min="0"
              max="20000"
              step="500"
              v-model.number="budget"
              class="budget-slider"
            >
            <span class="slider-bound">{{ formatCurrency(20000, currentCurrency) }}</span>
          </div>
          <div class="budget-value">{{ formatCurrency(budget, currentCurrency) }}</div>

          <div class="stats-grid">
            <div class="stat-card success">
              <div class="stat-label">{{ t('restocking.allocated') }}</div>
              <div class="stat-value">{{ formatCurrency(allocatedTotal, currentCurrency) }}</div>
            </div>
            <div class="stat-card info">
              <div class="stat-label">{{ t('restocking.remaining') }}</div>
              <div class="stat-value">{{ formatCurrency(remainingBudget, currentCurrency) }}</div>
            </div>
            <div class="stat-card">
              <div class="stat-label">{{ t('restocking.fundedItems') }}</div>
              <div class="stat-value">{{ fundedItems.length }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Success banner after order placement -->
      <div v-if="placedOrder" class="order-success-banner">
        <span>{{ t('restocking.orderPlaced', { orderNumber: placedOrder.order_number }) }}</span>
        <router-link to="/orders">{{ t('restocking.viewInOrders') }}</router-link>
      </div>

      <!-- Recommendations table -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendationsTitle') }}</h3>
        </div>

        <div v-if="recommendations.length === 0" class="no-data">
          {{ t('restocking.noRecommendations') }}
        </div>
        <template v-else>
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>{{ t('restocking.table.rank') }}</th>
                  <th>{{ t('restocking.table.sku') }}</th>
                  <th>{{ t('restocking.table.item') }}</th>
                  <th>{{ t('restocking.table.trend') }}</th>
                  <th>{{ t('restocking.table.currentDemand') }}</th>
                  <th>{{ t('restocking.table.forecastedDemand') }}</th>
                  <th>{{ t('restocking.table.gap') }}</th>
                  <th>{{ t('restocking.table.gapPercent') }}</th>
                  <th>{{ t('restocking.table.unitCost') }}</th>
                  <th>{{ t('restocking.table.orderQty') }}</th>
                  <th>{{ t('restocking.table.estCost') }}</th>
                  <th>{{ t('restocking.table.leadTime') }}</th>
                  <th>{{ t('restocking.table.fundingStatus') }}</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="(rec, index) in recommendations" :key="rec.sku">
                  <tr :class="{ 'unfunded-row': !rec.funded }">
                    <td>{{ index + 1 }}</td>
                    <td><strong>{{ rec.sku }}</strong></td>
                    <td>{{ rec.name }}</td>
                    <td>
                      <span :class="['badge', rec.trend]">
                        {{ t(`trends.${rec.trend}`) }}
                      </span>
                    </td>
                    <td>{{ rec.current_demand }}</td>
                    <td>{{ rec.forecasted_demand }}</td>
                    <td>{{ rec.gap }}</td>
                    <td>{{ rec.gap_pct }}%</td>
                    <td>{{ formatCurrency(rec.unit_cost, currentCurrency) }}</td>
                    <td>{{ rec.gap }}</td>
                    <td><strong>{{ formatCurrency(rec.estimated_cost, currentCurrency) }}</strong></td>
                    <td>{{ t('orders.leadTimeDays', { days: rec.lead_time_days }) }}</td>
                    <td>
                      <span :class="['badge', rec.funded ? 'success' : 'dimmed']">
                        {{ rec.funded ? t('restocking.funded') : t('restocking.notFunded') }}
                      </span>
                    </td>
                  </tr>
                  <!-- Divider row marks the strict budget cut-off point between the
                       last funded item and the first unfunded item -->
                  <tr v-if="isCutoffRow(rec, index)" class="cutoff-row">
                    <td colspan="13">{{ t('restocking.cutoffLabel') }}</td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>

          <div class="place-order-row">
            <button
              class="place-order-btn"
              :disabled="fundedItems.length === 0 || placingOrder"
              @click="placeOrder"
            >
              {{ placingOrder ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
            </button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency } from '../utils/currency'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency } = useI18n()

    const loading = ref(true)
    const error = ref(null)
    const allRecommendations = ref([])

    const budget = ref(10000)
    const placingOrder = ref(false)
    const placedOrder = ref(null)

    const loadRecommendations = async () => {
      try {
        loading.value = true
        error.value = null
        allRecommendations.value = await api.getRestockingRecommendations()
      } catch (err) {
        error.value = 'Failed to load restocking recommendations: ' + err.message
      } finally {
        loading.value = false
      }
    }

    // Greedy budget allocation with a STRICT cut-off: walk the server-ranked
    // recommendations in order, accumulating cost. Once an item doesn't fit
    // the remaining budget, funding stops permanently — later items are never
    // funded even if they'd individually fit — so there's a single clean
    // cut-off line in the table rather than a scattered mix of funded/unfunded rows.
    const recommendations = computed(() => {
      let remaining = budget.value
      let cutoffReached = false

      return allRecommendations.value.map(rec => {
        if (!cutoffReached && rec.estimated_cost <= remaining) {
          remaining -= rec.estimated_cost
          return { ...rec, funded: true }
        }
        cutoffReached = true
        return { ...rec, funded: false }
      })
    })

    const fundedItems = computed(() => recommendations.value.filter(r => r.funded))

    const allocatedTotal = computed(() =>
      fundedItems.value.reduce((sum, r) => sum + r.estimated_cost, 0)
    )

    const remainingBudget = computed(() => budget.value - allocatedTotal.value)

    // True only for the funded row immediately preceding the first unfunded row
    const isCutoffRow = (rec, index) => {
      if (!rec.funded) return false
      const next = recommendations.value[index + 1]
      return !!next && !next.funded
    }

    const placeOrder = async () => {
      if (fundedItems.value.length === 0) return
      placingOrder.value = true
      error.value = null
      try {
        const order = await api.createRestockingOrder({
          budget: budget.value,
          items: fundedItems.value.map(r => ({ sku: r.sku, quantity: r.gap }))
        })
        placedOrder.value = order
      } catch (err) {
        error.value = 'Failed to place restocking order: ' + err.message
      } finally {
        placingOrder.value = false
      }
    }

    onMounted(loadRecommendations)

    return {
      t,
      currentCurrency,
      formatCurrency,
      loading,
      error,
      budget,
      recommendations,
      fundedItems,
      allocatedTotal,
      remainingBudget,
      isCutoffRow,
      placingOrder,
      placedOrder,
      placeOrder
    }
  }
}
</script>

<style scoped>
.budget-card {
  margin-bottom: 1.5rem;
}

.budget-content {
  padding: 1.5rem;
}

.budget-slider-row {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 0.75rem;
}

.slider-bound {
  font-size: 0.813rem;
  color: #64748b;
  font-weight: 600;
  min-width: 3.5rem;
  flex-shrink: 0;
}

.budget-slider {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
  accent-color: #3b82f6;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
}

.budget-slider::-webkit-slider-runnable-track {
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
}

.budget-slider::-webkit-slider-thumb {
  appearance: none;
  -webkit-appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #3b82f6;
  cursor: pointer;
  margin-top: -6px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
}

.budget-slider::-moz-range-track {
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
}

.budget-slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #3b82f6;
  border: none;
  cursor: pointer;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
}

.budget-value {
  font-size: 1.75rem;
  font-weight: 700;
  color: #0f172a;
  text-align: center;
  margin-bottom: 1.25rem;
}

.no-data {
  padding: 2rem;
  text-align: center;
  color: #94a3b8;
  font-size: 0.875rem;
}

.unfunded-row {
  opacity: 0.5;
}

.cutoff-row td {
  border-top: 2px dashed #cbd5e1;
  border-bottom: none;
  text-align: center;
  color: #64748b;
  font-size: 0.813rem;
  font-style: italic;
  padding: 0.5rem;
  background: #f8fafc;
}

.badge.dimmed {
  background: #f1f5f9;
  color: #64748b;
}

.place-order-row {
  padding: 1.25rem;
  display: flex;
  justify-content: flex-end;
}

.place-order-btn {
  padding: 0.75rem 1.5rem;
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 8px;
  font-weight: 600;
  font-size: 0.938rem;
  cursor: pointer;
  transition: background 0.2s ease, transform 0.2s ease;
}

.place-order-btn:hover:not(:disabled) {
  background: #2563eb;
  transform: translateY(-1px);
}

.place-order-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.order-success-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #166534;
  padding: 1rem 1.25rem;
  border-radius: 8px;
  margin-bottom: 1.5rem;
  font-size: 0.938rem;
}

.order-success-banner a {
  color: #166534;
  font-weight: 600;
  text-decoration: underline;
  white-space: nowrap;
}
</style>
