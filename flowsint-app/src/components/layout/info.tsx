import { memo } from 'react'
import { HelpCircle } from 'lucide-react'
import { Dialog, DialogContent, DialogTrigger } from '../ui/dialog'
import { Button } from '../ui/button'

import { t } from '@/i18n'
const InfoDialog = () => {
  return (
    <>
      <Dialog>
        <DialogTrigger asChild>
          <div>
            <Button variant="ghost" size="sm" className="h-6 gap-1 text-xs">
              <HelpCircle className="h-3 w-3 opacity-60" />
            </Button>
          </div>
        </DialogTrigger>
        <DialogContent className="sm:max-w-2xl">
          <div className="p-2">
            <div className="p-2 text-sm space-y-4 overflow-y-auto max-h-[80vh]">
              <h2 className="text-base font-semibold flex items-center gap-2">
                {t('About Flowsint')}
              </h2>
              <p>
                <strong>{t('Flowsint')}</strong> {t('is an')}{' '}
                <strong>{t('investigation and intelligence platform')}</strong>{' '}
                {t('built to support complex research workflows involving')}{' '}
                <strong>{t('people, organizations, infrastructure, and online activity')}</strong>.
              </p>

              <p>
                Whether you&apos;re conducting <strong>{t('cyber investigations')}</strong>
                {t(', mapping out')} <strong>{t('fraud networks')}</strong>
                {t(', or gathering intelligence for')} <strong>{t('threat assessments')}</strong>
                {t(
                  ', Flowsint helps you collect, visualize, and understand fragmented data points in a structured and interactive way.'
                )}
              </p>

              <h3 className="font-semibold">{t('What Flowsint Does')}</h3>
              <ul className="list-disc list-inside space-y-1">
                <li>
                  <strong>{t('Connects scattered data')}</strong>{' '}
                  {t(
                    '— emails, domains, social accounts, IPs, phone numbers, addresses, and more — into a single'
                  )}{' '}
                  <strong>{t('investigative graph')}</strong>.
                </li>
                <li>
                  {t('Offers')} <strong>{t('visual enrichers')}</strong>{' '}
                  {t('to pivot from one entity to related ones: find')}{' '}
                  <strong>{t('connected individuals')}</strong>
                  {t(', discover')} <strong>{t('infrastructure')}</strong>
                  {t(', uncover')} <strong>{t('aliases')}</strong>.
                </li>
                <li>
                  <strong>{t('Tracks and saves investigation states')}</strong>{' '}
                  {t(
                    'over time, letting you explore multiple hypotheses or revisit older threads without losing context.'
                  )}
                </li>
                <li>
                  {t('Supports')} <strong>{t('live data enrichment')}</strong>{' '}
                  {t('from custom or built-in enrichers, giving you')}{' '}
                  <strong>{t('actionable insights')}</strong> {t('as you explore.')}
                </li>
              </ul>

              <h3 className="font-semibold">{t('Why Use Flowsint?')}</h3>
              <ul className="list-disc list-inside space-y-1">
                <li>
                  {t('Built for')} <strong>{t('speed and clarity')}</strong>{' '}
                  {t('— fast graph rendering, clean UI, responsive enrichers.')}
                </li>
                <li>
                  <strong>{t('Flexible graph model')}</strong>{' '}
                  {t('that mirrors how investigators think — not just tables and tags, but')}{' '}
                  <strong>{t('relationships')}</strong>.
                </li>
                <li>
                  {t('Ideal for')} <strong>{t('solo analysts and teams')}</strong>{' '}
                  {t('that need to move fast, explore freely, and make sense of')}{' '}
                  <strong>{t('partial or messy data')}</strong>.
                </li>
              </ul>

              <h3 className="font-semibold">{t('Use Cases')}</h3>
              <ul className="list-disc list-inside space-y-1">
                <li>
                  {t('Mapping')} <strong>{t('digital infrastructure')}</strong>{' '}
                  {t('of individuals or organizations')}
                </li>
                <li>
                  {t('Investigating')} <strong>{t('online fraud schemes')}</strong> {t('or')}{' '}
                  <strong>{t('fake identities')}</strong>
                </li>
                <li>
                  {t('Uncovering')} <strong>{t('links between actors')}</strong>{' '}
                  {t('across platforms')}
                </li>
                <li>
                  {t('Visualizing the reach of')} <strong>{t('leaked or exposed data')}</strong>
                </li>
                <li>
                  {t('Tracking')} <strong>{t('threat actor behavior')}</strong>{' '}
                  {t('across social and technical surfaces')}
                </li>
              </ul>

              <p>
                {t('Flowsint is designed for')} <strong>{t('professionals')}</strong>{' '}
                {t('who need')} <strong>{t('full control')}</strong> over their investigation logic,
                from how data is structured to how relationships are interpreted. It&apos;s not just
                a tool — it&apos;s a <strong>{t('flexible workspace')}</strong>{' '}
                {t('for building intelligence.')}
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

export default memo(InfoDialog)
