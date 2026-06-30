import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { CheckIcon, Loader2Icon, PlusIcon, RefreshCwIcon, SearchIcon, Settings2Icon, Trash2Icon, XIcon } from 'lucide-react'

import {
  createEntityType,
  deleteEntityType,
  EntityTypeItem,
  getEntityTypes,
  updateEntityType
} from '@/api/lightrag'
import Button from '@/components/ui/Button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle
} from '@/components/ui/Dialog'
import Input from '@/components/ui/Input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from '@/components/ui/Table'
import Textarea from '@/components/ui/Textarea'
import { errorMessage } from '@/lib/utils'

interface EntityTypesDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

type EntityTypeDraft = {
  name: string
  label: string
  description: string
}

type EntityTypeEditDraft = {
  label: string
  description: string
}

const emptyDraft: EntityTypeDraft = {
  name: '',
  label: '',
  description: ''
}

const isImmutableEntityType = (name: string): boolean => name === 'Other'

export default function EntityTypesDialog({
  open,
  onOpenChange
}: EntityTypesDialogProps) {
  const { t } = useTranslation()

  // Entity type rows loaded from the backend registry; this is the table's single source of truth.
  const [entityTypes, setEntityTypes] = useState<EntityTypeItem[]>([])
  // Workspace name returned by the server; it helps operators verify which isolated registry they are editing.
  const [workspace, setWorkspace] = useState<string>('')
  // Keyword typed by the operator; filtering is intentionally frontend-only over the current active list.
  const [filterKeyword, setFilterKeyword] = useState<string>('')
  // Tracks the list request lifecycle; it disables refresh controls and shows an explicit loading row.
  const [isLoading, setIsLoading] = useState<boolean>(false)
  // Tracks create/update/delete requests; it prevents double submits while the backend is writing the registry file.
  const [isSaving, setIsSaving] = useState<boolean>(false)
  // Draft for the create form; each field maps directly to the POST /entity-types request body.
  const [newType, setNewType] = useState<EntityTypeDraft>(emptyDraft)
  // Name of the row currently in edit mode; null means every row is rendered read-only.
  const [editingName, setEditingName] = useState<string | null>(null)
  // Draft for editable row fields; name is deliberately excluded because the backend does not support renaming.
  const [editDraft, setEditDraft] = useState<EntityTypeEditDraft>({ label: '', description: '' })

  const filteredEntityTypes = useMemo(
    () => {
      const keyword = filterKeyword.trim().toLowerCase()
      const activeTypes = entityTypes.filter((entityType) => entityType.status === 'active')

      if (!keyword) {
        return activeTypes.sort((left, right) => left.name.localeCompare(right.name))
      }

      return activeTypes
        .filter((entityType) => [
          entityType.name,
          entityType.label,
          entityType.description
        ].some((value) => (value || '').toLowerCase().includes(keyword)))
        .sort((left, right) => left.name.localeCompare(right.name))
    },
    [entityTypes, filterKeyword]
  )

  const resetCreateForm = useCallback(() => {
    setNewType(emptyDraft)
  }, [])

  const applyRegistryResponse = useCallback((response: { workspace?: string, entity_types?: EntityTypeItem[] }) => {
    setWorkspace(response.workspace || '')
    setEntityTypes(Array.isArray(response.entity_types) ? response.entity_types : [])
  }, [])

  const refreshEntityTypes = useCallback(async (showLoading: boolean = true) => {
    if (showLoading) {
      setIsLoading(true)
    }
    try {
      const response = await getEntityTypes(false)
      applyRegistryResponse(response)
    } catch (err) {
      toast.error(t('entityTypes.errors.loadFailed', { error: errorMessage(err) }))
    } finally {
      if (showLoading) {
        setIsLoading(false)
      }
    }
  }, [applyRegistryResponse, t])

  const handleDialogOpenChange = useCallback((nextOpen: boolean) => {
    if (!nextOpen) {
      // Closing the dialog discards transient form state so the next open starts from the latest saved registry.
      setEditingName(null)
      setEditDraft({ label: '', description: '' })
      setFilterKeyword('')
      resetCreateForm()
    }
    onOpenChange(nextOpen)
  }, [onOpenChange, resetCreateForm])

  useEffect(() => {
    if (!open) {
      return
    }

    // Queue the initial load outside the synchronous effect body; the project's
    // React lint rule forbids immediate state changes during effect execution.
    const timer = setTimeout(() => {
      refreshEntityTypes()
    }, 0)

    return () => clearTimeout(timer)
  }, [open, refreshEntityTypes])

  const handleCreate = useCallback(async () => {
    const name = newType.name.trim()
    const label = newType.label.trim()
    const description = newType.description.trim()

    if (!name) {
      toast.error(t('entityTypes.errors.nameRequired'))
      return
    }

    setIsSaving(true)
    try {
      await createEntityType({ name, label, description })
      await refreshEntityTypes(false)
      resetCreateForm()
      toast.success(t('entityTypes.messages.created'))
    } catch (err) {
      toast.error(t('entityTypes.errors.createFailed', { error: errorMessage(err) }))
    } finally {
      setIsSaving(false)
    }
  }, [newType.description, newType.label, newType.name, refreshEntityTypes, resetCreateForm, t])

  const startEdit = useCallback((entityType: EntityTypeItem) => {
    setEditingName(entityType.name)
    setEditDraft({
      label: entityType.label,
      description: entityType.description
    })
  }, [])

  const cancelEdit = useCallback(() => {
    setEditingName(null)
    setEditDraft({ label: '', description: '' })
  }, [])

  const handleUpdate = useCallback(async (name: string) => {
    setIsSaving(true)
    try {
      await updateEntityType(name, {
        label: editDraft.label.trim(),
        description: editDraft.description.trim()
      })
      await refreshEntityTypes(false)
      cancelEdit()
      toast.success(t('entityTypes.messages.updated'))
    } catch (err) {
      toast.error(t('entityTypes.errors.updateFailed', { error: errorMessage(err) }))
    } finally {
      setIsSaving(false)
    }
  }, [cancelEdit, editDraft.description, editDraft.label, refreshEntityTypes, t])

  const handleDelete = useCallback(async (entityType: EntityTypeItem) => {
    if (isImmutableEntityType(entityType.name)) {
      toast.error(t('entityTypes.errors.otherCannotDelete'))
      return
    }

    setIsSaving(true)
    try {
      await deleteEntityType(entityType.name)
      await refreshEntityTypes(false)
      toast.success(t('entityTypes.messages.deleted'))
    } catch (err) {
      toast.error(t('entityTypes.errors.deleteFailed', { error: errorMessage(err) }))
    } finally {
      setIsSaving(false)
    }
  }, [refreshEntityTypes, t])

  return (
    <Dialog open={open} onOpenChange={handleDialogOpenChange}>
      <DialogContent className="max-h-[88vh] overflow-hidden sm:max-w-[980px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Settings2Icon className="h-5 w-5" />
            {t('entityTypes.title')}
          </DialogTitle>
          <DialogDescription>
            {workspace
              ? t('entityTypes.descriptionWithWorkspace', { workspace })
              : t('entityTypes.description')}
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-col gap-4">
          <div className="rounded-md border p-3">
            <div className="grid gap-3 md:grid-cols-[minmax(120px,180px)_minmax(120px,180px)_1fr_auto]">
              <Input
                value={newType.name}
                onChange={(event) => setNewType((current) => ({ ...current, name: event.target.value }))}
                placeholder={t('entityTypes.fields.name')}
                disabled={isSaving}
              />
              <Input
                value={newType.label}
                onChange={(event) => setNewType((current) => ({ ...current, label: event.target.value }))}
                placeholder={t('entityTypes.fields.label')}
                disabled={isSaving}
              />
              <Input
                value={newType.description}
                onChange={(event) => setNewType((current) => ({ ...current, description: event.target.value }))}
                placeholder={t('entityTypes.fields.description')}
                disabled={isSaving}
              />
              <Button onClick={handleCreate} disabled={isSaving}>
                {isSaving ? <Loader2Icon className="h-4 w-4 animate-spin" /> : <PlusIcon className="h-4 w-4" />}
                {t('entityTypes.actions.create')}
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="relative min-w-[240px] flex-1">
              <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={filterKeyword}
                onChange={(event) => setFilterKeyword(event.target.value)}
                placeholder={t('entityTypes.actions.filterPlaceholder')}
                disabled={isLoading || isSaving}
                className="pl-8"
              />
            </div>
            <Button variant="outline" size="sm" onClick={refreshEntityTypes} disabled={isLoading || isSaving}>
              {isLoading ? <Loader2Icon className="h-4 w-4 animate-spin" /> : <RefreshCwIcon className="h-4 w-4" />}
              {t('entityTypes.actions.refresh')}
            </Button>
          </div>

          <div className="max-h-[48vh] min-h-[240px] overflow-y-auto rounded-md border">
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-background">
                <TableRow>
                  <TableHead className="w-[150px]">{t('entityTypes.fields.name')}</TableHead>
                  <TableHead className="w-[170px]">{t('entityTypes.fields.label')}</TableHead>
                  <TableHead>{t('entityTypes.fields.description')}</TableHead>
                  <TableHead className="w-[180px] text-right">{t('entityTypes.fields.actions')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <TableRow>
                    <TableCell colSpan={4} className="h-24 text-center text-muted-foreground">
                      {t('entityTypes.messages.loading')}
                    </TableCell>
                  </TableRow>
                ) : filteredEntityTypes.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={4} className="h-24 text-center text-muted-foreground">
                      {filterKeyword.trim()
                        ? t('entityTypes.messages.noFilterResults')
                        : t('entityTypes.messages.empty')}
                    </TableCell>
                  </TableRow>
                ) : filteredEntityTypes.map((entityType) => {
                  const isEditing = editingName === entityType.name
                  const disableDelete = isImmutableEntityType(entityType.name) || isSaving

                  return (
                    <TableRow key={entityType.name}>
                      <TableCell className="font-mono text-xs">{entityType.name}</TableCell>
                      <TableCell>
                        {isEditing ? (
                          <Input
                            value={editDraft.label}
                            onChange={(event) => setEditDraft((current) => ({ ...current, label: event.target.value }))}
                            disabled={isSaving}
                          />
                        ) : entityType.label || '-'}
                      </TableCell>
                      <TableCell>
                        {isEditing ? (
                          <Textarea
                            value={editDraft.description}
                            onChange={(event) => setEditDraft((current) => ({ ...current, description: event.target.value }))}
                            className="min-h-[72px]"
                            disabled={isSaving}
                          />
                        ) : (
                          <span className="line-clamp-3 text-sm">{entityType.description || '-'}</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex justify-end gap-2">
                          {isEditing ? (
                            <>
                              <Button size="icon" variant="ghost" onClick={() => handleUpdate(entityType.name)} disabled={isSaving} tooltip={t('entityTypes.actions.save')}>
                                <CheckIcon className="h-4 w-4" />
                              </Button>
                              <Button size="icon" variant="ghost" onClick={cancelEdit} disabled={isSaving} tooltip={t('entityTypes.actions.cancel')}>
                                <XIcon className="h-4 w-4" />
                              </Button>
                            </>
                          ) : (
                            <>
                              <Button size="sm" variant="outline" onClick={() => startEdit(entityType)} disabled={isSaving}>
                                {t('entityTypes.actions.edit')}
                              </Button>
                              <Button size="icon" variant="ghost" onClick={() => handleDelete(entityType)} disabled={disableDelete} tooltip={t('entityTypes.actions.delete')}>
                                <Trash2Icon className="h-4 w-4" />
                              </Button>
                            </>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
